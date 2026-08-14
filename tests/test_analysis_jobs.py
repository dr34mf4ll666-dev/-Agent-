import sys
import time
import unittest
import tempfile
import json
from pathlib import Path
from threading import Event


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.analysis_jobs import AnalysisJobError, AnalysisJobRuntime  # noqa: E402
from agent_platform.client_app import ClientAnalysisRequest, ClientAnalysisResult  # noqa: E402
from agent_platform.analysis_repository import (  # noqa: E402
    AnalysisRepositoryError,
    InMemoryAnalysisRepository,
)


class _ControlledWorker:
    def __init__(self, *, fail=False, wait=False):
        self.fail = fail
        self.wait = wait
        self.started = Event()
        self.release = Event()

    def run(self, request, progress, **kwargs):
        progress("c1_research", "running", 1, "research")
        self.started.set()
        if self.wait:
            self.release.wait(timeout=2)
        if self.fail:
            raise RuntimeError("provider timed out")
        progress("c1_research", "completed", 1, "research")
        progress("chart", "running", 1, "chart")
        progress("chart", "completed", 1, "chart")
        progress("report", "running", 1, "report")
        progress("report", "completed", 1, "report")
        return ClientAnalysisResult(
            {"symbol": request.symbol, "simulation_only": True},
            debate_context={"reports": {}},
        )


def _wait_for(runtime, job_id, expected, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runtime.get(job_id)
        if job["status"] in expected:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {expected}: {runtime.get(job_id)}")


class AnalysisJobRuntimeTests(unittest.TestCase):
    def test_success_is_archived_before_job_becomes_visible(self):
        repository = InMemoryAnalysisRepository()
        runtime = AnalysisJobRuntime(_ControlledWorker(), max_workers=1, repository=repository)
        self.addCleanup(runtime.close)

        submitted = runtime.submit(ClientAnalysisRequest())
        completed = _wait_for(runtime, submitted["job_id"], {"succeeded"})
        result = runtime.result(submitted["job_id"]).to_mapping()

        self.assertEqual(result["report_version"], 1)
        self.assertEqual(len(result["report_id"]), 32)
        self.assertEqual(repository.list_reports()[0]["job_id"], completed["job_id"])

    def test_repository_failure_keeps_job_failed_and_exposes_no_result(self):
        class _FailingRepository(InMemoryAnalysisRepository):
            def archive(self, value):
                raise AnalysisRepositoryError("injected database failure")

        runtime = AnalysisJobRuntime(_ControlledWorker(), max_workers=1, repository=_FailingRepository())
        self.addCleanup(runtime.close)
        submitted = runtime.submit(ClientAnalysisRequest())

        failed = _wait_for(runtime, submitted["job_id"], {"failed"})

        self.assertIn("database failure", failed["error"]["message"])
        self.assertEqual(failed["error"]["trace_id"], submitted["trace_id"])
        self.assertIn("只重试失败步骤", failed["error"]["user_action"])
        trace = runtime.observability.trace(submitted["trace_id"])
        self.assertEqual(trace["status"], "failed")
        self.assertTrue(any(
            span["layer"] == "database" and span["status"] == "failed"
            for span in trace["spans"]
        ))
        with self.assertRaisesRegex(AnalysisJobError, "尚无可用结果"):
            runtime.result(submitted["job_id"])

    def test_delete_completed_removes_job_json_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = AnalysisJobRuntime(
                _ControlledWorker(), max_workers=1,
                storage_path=root / "jobs.json", checkpoint_root=root / "checkpoints",
            )
            self.addCleanup(runtime.close)
            submitted = runtime.submit(ClientAnalysisRequest())
            _wait_for(runtime, submitted["job_id"], {"succeeded"})
            checkpoint = root / "checkpoints" / submitted["job_id"] / "0"
            checkpoint.mkdir(parents=True, exist_ok=True)
            (checkpoint / "evidence.json").write_text("{}", encoding="utf-8")

            removed = runtime.delete_completed(submitted["job_id"])

            self.assertTrue(removed)
            self.assertFalse(checkpoint.parent.exists())
            payload = json.loads((root / "jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["jobs"], [])
            with self.assertRaisesRegex(AnalysisJobError, "不存在"):
                runtime.get(submitted["job_id"])

    def test_running_job_cannot_be_deleted(self):
        worker = _ControlledWorker(wait=True)
        runtime = AnalysisJobRuntime(worker, max_workers=1)
        self.addCleanup(runtime.close)
        submitted = runtime.submit(ClientAnalysisRequest())
        self.assertTrue(worker.started.wait(timeout=1))

        with self.assertRaisesRegex(AnalysisJobError, "运行中"):
            runtime.delete_completed(submitted["job_id"])
        worker.release.set()

    def test_submit_returns_job_and_success_exposes_result(self):
        runtime = AnalysisJobRuntime(_ControlledWorker(), max_workers=1)
        self.addCleanup(runtime.close)

        submitted = runtime.submit(ClientAnalysisRequest())
        completed = _wait_for(runtime, submitted["job_id"], {"succeeded"})
        result = runtime.result(submitted["job_id"])

        self.assertEqual(len(submitted["job_id"]), 32)
        self.assertEqual(len(submitted["trace_id"]), 32)
        self.assertEqual(completed["progress"]["percent"], 100)
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(result.to_mapping()["trace_id"], submitted["trace_id"])
        self.assertEqual(result.to_mapping()["symbol"], "sz000001")
        self.assertEqual(completed["persistence"], "memory_only")

    def test_result_is_guarded_until_job_succeeds(self):
        worker = _ControlledWorker(wait=True)
        runtime = AnalysisJobRuntime(worker, max_workers=1)
        self.addCleanup(runtime.close)
        submitted = runtime.submit(ClientAnalysisRequest())
        self.assertTrue(worker.started.wait(timeout=1))

        with self.assertRaisesRegex(AnalysisJobError, "尚无可用结果"):
            runtime.result(submitted["job_id"])

        worker.release.set()
        _wait_for(runtime, submitted["job_id"], {"succeeded"})

    def test_worker_failure_is_normalized_and_running_stage_fails(self):
        runtime = AnalysisJobRuntime(_ControlledWorker(fail=True), max_workers=1)
        self.addCleanup(runtime.close)
        submitted = runtime.submit(ClientAnalysisRequest())

        failed = _wait_for(runtime, submitted["job_id"], {"failed"})

        self.assertEqual(failed["error"]["type"], "RuntimeError")
        self.assertEqual(failed["error"]["message"], "provider timed out")
        self.assertEqual(failed["progress"]["stages"][0]["status"], "failed")

    def test_running_cancel_stops_at_next_safe_progress_point(self):
        worker = _ControlledWorker(wait=True)
        runtime = AnalysisJobRuntime(worker, max_workers=1)
        self.addCleanup(runtime.close)
        submitted = runtime.submit(ClientAnalysisRequest())
        self.assertTrue(worker.started.wait(timeout=1))

        cancelling = runtime.cancel(submitted["job_id"])
        self.assertTrue(cancelling["cancel_requested"])
        worker.release.set()
        cancelled = _wait_for(runtime, submitted["job_id"], {"cancelled"})

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(all(stage["status"] in {"failed", "cancelled"} for stage in cancelled["progress"]["stages"]))

    def test_queued_job_can_be_cancelled_before_worker_starts(self):
        first_worker = _ControlledWorker(wait=True)
        runtime = AnalysisJobRuntime(first_worker, max_workers=1)
        self.addCleanup(runtime.close)
        first = runtime.submit(ClientAnalysisRequest())
        self.assertTrue(first_worker.started.wait(timeout=1))
        second = runtime.submit(ClientAnalysisRequest())

        cancelled = runtime.cancel(second["job_id"])

        self.assertEqual(cancelled["status"], "cancelled")
        first_worker.release.set()
        _wait_for(runtime, first["job_id"], {"succeeded"})

    def test_failed_job_can_retry_and_preserves_retry_count(self):
        worker = _ControlledWorker(fail=True)
        runtime = AnalysisJobRuntime(worker, max_workers=1)
        self.addCleanup(runtime.close)
        submitted = runtime.submit(ClientAnalysisRequest())
        _wait_for(runtime, submitted["job_id"], {"failed"})

        worker.fail = False
        retried = runtime.retry(submitted["job_id"])
        completed = _wait_for(runtime, submitted["job_id"], {"succeeded"})

        self.assertEqual(retried["retry_count"], 1)
        self.assertEqual(completed["retry_count"], 1)

    def test_total_timeout_fails_job_and_ignores_late_result(self):
        worker = _ControlledWorker(wait=True)
        runtime = AnalysisJobRuntime(worker, max_workers=1, timeout_seconds=0.05)
        self.addCleanup(runtime.close)
        submitted = runtime.submit(ClientAnalysisRequest())
        failed = _wait_for(runtime, submitted["job_id"], {"failed"})

        self.assertEqual(failed["error"]["type"], "AnalysisJobTimeout")
        self.assertTrue(failed["can_retry"])
        worker.release.set()
        time.sleep(0.05)
        self.assertEqual(runtime.get(submitted["job_id"])["status"], "failed")

    def test_retry_generation_rejects_old_timed_out_callbacks(self):
        first = _ControlledWorker(wait=True)

        class _GenerationWorker:
            def __init__(self):
                self.calls = 0

            def run(self, request, progress, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return first.run(request, progress, **kwargs)
                progress("c1_research", "running", 2, "retry")
                progress("c1_research", "completed", 2, "retry")
                progress("chart", "running", 2, "retry")
                progress("chart", "completed", 2, "retry")
                progress("report", "running", 2, "retry")
                progress("report", "completed", 2, "retry")
                return ClientAnalysisResult({"symbol": request.symbol, "generation": 2})

        runtime = AnalysisJobRuntime(_GenerationWorker(), max_workers=2, timeout_seconds=0.05)
        self.addCleanup(runtime.close)
        submitted = runtime.submit(ClientAnalysisRequest())
        _wait_for(runtime, submitted["job_id"], {"failed"})
        runtime.retry(submitted["job_id"])
        completed = _wait_for(runtime, submitted["job_id"], {"succeeded"})
        first.release.set()
        time.sleep(0.05)

        self.assertEqual(runtime.result(submitted["job_id"]).to_mapping()["generation"], 2)
        self.assertEqual(completed["retry_count"], 1)

    def test_json_store_restores_successful_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.json"
            runtime = AnalysisJobRuntime(_ControlledWorker(), storage_path=path)
            submitted = runtime.submit(ClientAnalysisRequest())
            completed = _wait_for(runtime, submitted["job_id"], {"succeeded"})
            runtime.close()

            restored = AnalysisJobRuntime(_ControlledWorker(), storage_path=path)
            self.addCleanup(restored.close)

            self.assertEqual(restored.get(completed["job_id"])["status"], "succeeded")
            self.assertEqual(restored.result(completed["job_id"]).to_mapping()["symbol"], "sz000001")
            self.assertEqual(restored.get(completed["job_id"])["persistence"], "json")

    def test_startup_resumes_interrupted_job_from_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jobs.json"
            template = AnalysisJobRuntime(_ControlledWorker())
            stages = template._new_stages()
            template.close()
            payload = {
                "version": 1,
                "jobs": [{
                    "job_id": "a" * 32,
                    "request": {"symbol": "sz000001", "mode": "offline"},
                    "created_at": "2026-08-13T10:00:00+08:00",
                    "updated_at": "2026-08-13T10:00:01+08:00",
                    "status": "running",
                    "stages": stages,
                    "result": None,
                    "error": None,
                }],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            restored = AnalysisJobRuntime(_ControlledWorker(), storage_path=path)
            self.addCleanup(restored.close)
            completed = _wait_for(restored, "a" * 32, {"succeeded"})

            self.assertTrue(completed["recovered"])


if __name__ == "__main__":
    unittest.main()
