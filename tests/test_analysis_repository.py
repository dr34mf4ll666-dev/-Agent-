import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.analysis_repository import (  # noqa: E402
    AnalysisArchive,
    AnalysisRepositoryError,
    InMemoryAnalysisRepository,
    SQLiteAnalysisRepository,
)


def _archive(index=1, **changes):
    report_id = changes.pop("report_id", f"report-{index:02d}")
    job_id = changes.pop("job_id", f"job-{index:02d}")
    result = {
        "status": "succeeded",
        "security": {"symbol": "sz000001", "code": "000001", "name": "平安银行"},
        "data": {
            "mode": "offline", "label": "已验证历史快照",
            "as_of": "2026-08-07T15:00:00+08:00", "snapshot_id": f"snapshot-{index:02d}",
            "snapshot": {"snapshot_id": f"snapshot-{index:02d}"},
        },
        "verdict": {"label": "偏强", "action_label": "偏多关注"},
        "report_id": report_id,
        "report_version": 1,
    }
    values = {
        "report_id": report_id, "report_version": 1, "job_id": job_id,
        "symbol": "sz000001", "mode": "offline",
        "created_at": f"2026-08-13T10:00:{index:02d}+08:00",
        "archived_at": f"2026-08-13T10:01:{index:02d}+08:00",
        "task": {"status": "succeeded", "stages": [{"id": "report", "status": "completed"}]},
        "result": result, "debate_context": {"reports": {"technical": {"score": 10}}},
        "snapshot": {"snapshot_id": f"snapshot-{index:02d}", "datasets": []},
        "agents": {"technical": {"score": 10}},
        "graphs": {"financial": {"status": "completed"}},
    }
    values.update(changes)
    return AnalysisArchive(**values)


class InMemoryAnalysisRepositoryTests(unittest.TestCase):
    def test_memory_adapter_implements_archive_list_get_and_model_call(self):
        repository = InMemoryAnalysisRepository()
        repository.archive(_archive())
        repository.record_model_call(
            "report-01",
            {"provider": "local", "model": "guide", "status": "succeeded", "usage": {}, "latency_ms": 0},
        )
        repository.record_model_feedback(
            "report-01",
            {
                "rating": "helpful",
                "explanation_version": "local-rule-v1/local-explanation-v1",
                "provider": "local",
                "model": "guide",
                "created_at": "2026-08-14T10:00:00+08:00",
            },
        )

        self.assertEqual(repository.list_reports()[0]["snapshot_id"], "snapshot-01")
        self.assertEqual(repository.get_report("report-01")["agents"]["technical"]["score"], 10)
        self.assertEqual(len(repository.get_report("report-01")["model_calls"]), 1)
        self.assertEqual(
            repository.get_report("report-01")["model_feedback"][0]["rating"],
            "helpful",
        )

    def test_memory_adapter_deletes_one_or_clears_all(self):
        repository = InMemoryAnalysisRepository()
        repository.archive(_archive(1))
        repository.archive(_archive(2))

        deleted = repository.delete_report("report-01")
        cleared = repository.clear_reports()

        self.assertEqual(deleted, {"report_id": "report-01", "job_id": "job-01"})
        self.assertEqual(cleared, [{"report_id": "report-02", "job_id": "job-02"}])
        self.assertEqual(repository.list_reports(), [])


class SQLiteAnalysisRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.sqlite3"

    def test_migration_is_explicit_and_restart_recovers_report(self):
        repository = SQLiteAnalysisRepository(self.path)
        repository.archive(_archive())

        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
        restarted = SQLiteAnalysisRepository(self.path)

        report = restarted.get_report("report-01")
        self.assertEqual(report["result"]["data"]["snapshot_id"], "snapshot-01")
        self.assertEqual(report["task"]["status"], "succeeded")
        self.assertIn("technical", report["agents"])
        self.assertIn("financial", report["graphs"])

    def test_existing_version_one_database_migrates_to_current_version(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript(
                """
                CREATE TABLE analysis_reports (
                    report_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE,
                    report_version INTEGER NOT NULL, symbol TEXT NOT NULL, mode TEXT NOT NULL,
                    created_at TEXT NOT NULL, archived_at TEXT NOT NULL, result_json TEXT NOT NULL,
                    debate_context_json TEXT NOT NULL, checksum TEXT NOT NULL
                );
                CREATE TABLE model_calls (
                    call_id TEXT PRIMARY KEY, report_id TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
                    usage_json TEXT NOT NULL, latency_ms INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                PRAGMA user_version = 1;
                """
            )

        SQLiteAnalysisRepository(self.path)

        with closing(sqlite3.connect(self.path)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            columns = {row[1] for row in connection.execute("PRAGMA table_info(model_calls)")}
            feedback_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(model_feedback)")
            }
        self.assertEqual(version, 4)
        self.assertTrue({"kind", "output_json"}.issubset(columns))
        self.assertTrue({"rating", "explanation_version"}.issubset(feedback_columns))
        with closing(sqlite3.connect(self.path)) as connection:
            provenance_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(analysis_provenance)")
            }
        self.assertTrue({"quality_json", "identity_json", "fingerprint"}.issubset(provenance_columns))

    def test_distinct_reports_never_overwrite_each_other(self):
        repository = SQLiteAnalysisRepository(self.path)
        repository.archive(_archive(1))
        repository.archive(_archive(2))

        reports = repository.list_reports()

        self.assertEqual([item["report_id"] for item in reports], ["report-02", "report-01"])
        self.assertEqual(repository.get_report("report-01")["job_id"], "job-01")

    def test_provenance_is_archived_separately_and_legacy_report_stays_readable(self):
        repository = SQLiteAnalysisRepository(self.path)
        archive = _archive(
            provenance={
                "schema_version": 1,
                "quality": {
                    "overall_status": "complete",
                    "comparison_ready": True,
                },
                "identity": {"snapshot_id": "snapshot-01", "code_version": "0.1.0"},
                "fingerprint": "a" * 64,
            }
        )
        repository.archive(archive)
        repository.archive(_archive(2))

        self.assertEqual(
            repository.get_report("report-01")["provenance"]["fingerprint"], "a" * 64
        )
        self.assertIsNone(repository.get_report("report-02")["provenance"])

    def test_concurrent_writes_all_survive(self):
        repository = SQLiteAnalysisRepository(self.path, timeout_seconds=10)

        with ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(lambda index: repository.archive(_archive(index)), range(1, 21)))

        self.assertEqual(len(set(ids)), 20)
        self.assertEqual(len(repository.list_reports(limit=100)), 20)

    def test_write_failure_rolls_back_every_table(self):
        repository = SQLiteAnalysisRepository(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """CREATE TRIGGER fail_snapshot BEFORE INSERT ON analysis_snapshots
                   BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"""
            )

        with self.assertRaisesRegex(AnalysisRepositoryError, "完整保存"):
            repository.archive(_archive())

        with closing(sqlite3.connect(self.path)) as connection:
            counts = [
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("analysis_reports", "analysis_tasks", "analysis_snapshots", "analysis_agents", "analysis_graphs")
            ]
        self.assertEqual(counts, [0, 0, 0, 0, 0])

    def test_corrupted_report_is_rejected_by_checksum(self):
        repository = SQLiteAnalysisRepository(self.path)
        repository.archive(_archive())
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "UPDATE analysis_reports SET result_json = ? WHERE report_id = ?",
                ('{"tampered":true}', "report-01"),
            )
            connection.commit()

        with self.assertRaisesRegex(AnalysisRepositoryError, "完整性校验失败"):
            repository.get_report("report-01")

    def test_corrupted_agent_evidence_is_rejected_by_checksum(self):
        repository = SQLiteAnalysisRepository(self.path)
        repository.archive(_archive())
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "UPDATE analysis_agents SET payload_json = ? WHERE report_id = ?",
                ('{"score":999}', "report-01"),
            )
            connection.commit()

        with self.assertRaisesRegex(AnalysisRepositoryError, "完整性校验失败"):
            repository.get_report("report-01")

    def test_sensitive_key_is_rejected_before_database_write(self):
        repository = SQLiteAnalysisRepository(self.path)
        unsafe = _archive(task={"status": "succeeded", "DEEPSEEK_API_KEY": "should-never-save"})

        with self.assertRaisesRegex(AnalysisRepositoryError, "敏感字段"):
            repository.archive(unsafe)

        self.assertEqual(repository.list_reports(), [])

    def test_model_call_metadata_is_recovered_without_prompt_or_key(self):
        repository = SQLiteAnalysisRepository(self.path)
        repository.archive(_archive())
        repository.record_model_call(
            "report-01",
            {
                "provider": "deepseek", "model": "deepseek-v4-flash", "status": "succeeded",
                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                "latency_ms": 900, "created_at": "2026-08-13T10:02:00+08:00",
                "kind": "client_explanation", "output": {"headline": "已保存解读"},
            },
        )
        repository.record_model_feedback(
            "report-01",
            {
                "rating": "not_helpful",
                "explanation_version": "p7-policy-v1",
                "provider": "deepseek",
                "model": "deepseek-test",
                "created_at": "2026-08-13T10:03:00+08:00",
            },
        )

        call = SQLiteAnalysisRepository(self.path).get_report("report-01")["model_calls"][0]

        self.assertEqual(call["usage"]["total_tokens"], 120)
        self.assertEqual(call["output"]["headline"], "已保存解读")
        self.assertNotIn("prompt", call)
        self.assertNotIn("api_key", call)
        feedback = SQLiteAnalysisRepository(self.path).get_report("report-01")["model_feedback"]
        self.assertEqual(feedback[0]["rating"], "not_helpful")

    def test_delete_report_cascades_all_related_rows(self):
        repository = SQLiteAnalysisRepository(self.path)
        repository.archive(_archive())
        repository.record_model_call(
            "report-01",
            {"provider": "local", "model": "guide", "status": "succeeded", "usage": {}, "latency_ms": 0},
        )

        deleted = repository.delete_report("report-01")

        self.assertEqual(deleted["job_id"], "job-01")
        with closing(sqlite3.connect(self.path)) as connection:
            counts = [
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("analysis_reports", "analysis_tasks", "analysis_snapshots", "analysis_agents", "analysis_graphs", "model_calls", "model_feedback")
            ]
        self.assertEqual(counts, [0, 0, 0, 0, 0, 0, 0])

    def test_clear_reports_removes_every_report_and_related_row(self):
        repository = SQLiteAnalysisRepository(self.path)
        repository.archive(_archive(1))
        repository.archive(_archive(2))

        deleted = repository.clear_reports()

        self.assertEqual({item["job_id"] for item in deleted}, {"job-01", "job-02"})
        self.assertEqual(repository.list_reports(), [])


if __name__ == "__main__":
    unittest.main()
