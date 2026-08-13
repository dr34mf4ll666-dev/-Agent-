"""Persistent asynchronous jobs for customer-facing financial analysis."""

from __future__ import annotations

import json
import shutil
from collections import OrderedDict
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock, Timer
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from .client_app import ClientAnalysisRequest, ClientAnalysisResult, ClientAnalysisRuntime


class AnalysisJobError(ValueError):
    """An analysis job is unknown or not in the required state."""


class _AnalysisCancelled(RuntimeError):
    pass


class _AnalysisSuperseded(RuntimeError):
    pass


ProgressCallback = Callable[[str, str, int, str], None]


class AnalysisJobWorker(Protocol):
    def run(
        self,
        request: ClientAnalysisRequest,
        progress: ProgressCallback,
        *,
        checkpoint_dir: Path | None = None,
        resume: bool = False,
    ) -> ClientAnalysisResult:
        """Run one analysis while reporting real node transitions."""


class ClientAnalysisJobWorker:
    """Adapter between the job seam and the existing customer analysis module."""

    def __init__(self, runtime: ClientAnalysisRuntime) -> None:
        self._runtime = runtime

    def run(
        self,
        request: ClientAnalysisRequest,
        progress: ProgressCallback,
        *,
        checkpoint_dir: Path | None = None,
        resume: bool = False,
    ) -> ClientAnalysisResult:
        return self._runtime.analyze(
            request,
            progress=progress,
            checkpoint_dir=checkpoint_dir,
            resume=resume,
        )


@dataclass
class _JobRecord:
    job_id: str
    request: ClientAnalysisRequest
    created_at: str
    updated_at: str
    status: str
    stages: list[dict[str, Any]]
    result: ClientAnalysisResult | None = None
    error: dict[str, Any] | None = None
    cancel_requested: bool = False
    future: Future[Any] | None = None
    retry_count: int = 0
    recovered: bool = False
    resume: bool = False
    timed_out: bool = False
    generation: int = 0


_STAGES = (
    ("c1_research", "联合研究总流程", "setup"),
    ("planner", "制定四维研究计划", "setup"),
    ("technical", "技术走势 Agent", "specialist"),
    ("fundamental", "基本面 Agent", "specialist"),
    ("industry", "行业 Agent", "specialist"),
    ("macro", "宏观环境 Agent", "specialist"),
    ("aggregate", "汇集四维证据", "decision"),
    ("c1_debate", "多空观点辩论", "decision"),
    ("c1_quality", "一致性与偏差检查", "decision"),
    ("c1_synthesis", "形成综合研究结论", "decision"),
    ("trader", "生成模拟交易候选", "risk"),
    ("market_route", "选择市场风险路径", "risk"),
    ("risk_manager", "风险经理复核", "risk"),
    ("market_bearish_skip", "弱市买入阻断", "risk"),
    ("finalize", "完成安全决策", "risk"),
    ("chart", "整理行情与图表数据", "report"),
    ("report", "生成客户研究报告", "report"),
)

_TERMINAL = {"succeeded", "failed", "cancelled"}


class AnalysisJobRuntime:
    """Deep interface for submit, observe, retry, cancel, and retrieve jobs."""

    def __init__(
        self,
        worker: AnalysisJobWorker,
        *,
        max_workers: int = 2,
        max_jobs: int = 64,
        timeout_seconds: float = 180.0,
        storage_path: str | Path | None = None,
        checkpoint_root: str | Path | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        if isinstance(max_jobs, bool) or not isinstance(max_jobs, int) or max_jobs < 1:
            raise ValueError("max_jobs must be a positive integer")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._worker = worker
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="analysis-job")
        self._max_jobs = max_jobs
        self._timeout_seconds = float(timeout_seconds)
        self._storage_path = Path(storage_path).resolve() if storage_path is not None else None
        self._checkpoint_root = Path(checkpoint_root).resolve() if checkpoint_root is not None else None
        self._now = now or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))
        self._jobs: OrderedDict[str, _JobRecord] = OrderedDict()
        self._lock = Lock()
        self._load_and_resume()

    @classmethod
    def from_client_runtime(cls, runtime: ClientAnalysisRuntime, **kwargs: Any) -> "AnalysisJobRuntime":
        return cls(ClientAnalysisJobWorker(runtime), **kwargs)

    def submit(self, request: ClientAnalysisRequest) -> dict[str, Any]:
        if not isinstance(request, ClientAnalysisRequest):
            raise AnalysisJobError("request must be a ClientAnalysisRequest")
        job_id = uuid4().hex
        timestamp = self._timestamp()
        record = _JobRecord(
            job_id=job_id,
            request=request,
            created_at=timestamp,
            updated_at=timestamp,
            status="queued",
            stages=self._new_stages(),
        )
        with self._lock:
            self._evict_finished_jobs()
            if len(self._jobs) >= self._max_jobs:
                raise AnalysisJobError("分析任务已达到容量上限，请稍后再试。")
            self._jobs[job_id] = record
            self._persist_locked()
            record.future = self._executor.submit(self._execute, job_id)
            return self._snapshot(record)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._snapshot(self._record(job_id))

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._record(job_id)
            if record.status in _TERMINAL:
                return self._snapshot(record)
            record.cancel_requested = True
            record.updated_at = self._timestamp()
            if record.status == "queued" and record.future is not None and record.future.cancel():
                self._finish_cancelled(record)
            self._persist_locked()
            return self._snapshot(record)

    def retry(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._record(job_id)
            if record.status != "failed" or record.error is None or not record.error.get("retryable", False):
                raise AnalysisJobError("只有可重试的失败任务才能重新执行。")
            record.status = "queued"
            record.error = None
            record.cancel_requested = False
            record.timed_out = False
            record.result = None
            record.resume = True
            record.retry_count += 1
            previous_generation = record.generation
            record.generation += 1
            self._copy_checkpoint_generation(
                record.job_id, previous_generation, record.generation
            )
            record.updated_at = self._timestamp()
            for stage in record.stages:
                if stage["status"] in {"failed", "cancelled", "retrying", "running"}:
                    stage.update(status="pending", started_at=None, finished_at=None, detail="")
            self._persist_locked()
            record.future = self._executor.submit(self._execute, job_id)
            return self._snapshot(record)

    def result(self, job_id: str) -> ClientAnalysisResult:
        with self._lock:
            record = self._record(job_id)
            if record.status != "succeeded" or record.result is None:
                raise AnalysisJobError(f"分析任务尚无可用结果，当前状态为 {record.status}。")
            return record.result

    def close(self, *, wait: bool = True) -> None:
        with self._lock:
            self._persist_locked()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _execute(self, job_id: str) -> None:
        timer: Timer | None = None
        try:
            with self._lock:
                record = self._record(job_id)
                if record.cancel_requested:
                    self._finish_cancelled(record)
                    self._persist_locked()
                    return
                record.status = "running"
                record.updated_at = self._timestamp()
                self._persist_locked()
                resume = record.resume
                generation = record.generation
                checkpoint_dir = self._checkpoint_dir(record.job_id, generation)
            timer = Timer(
                self._timeout_seconds,
                self._timeout_job,
                args=(job_id, generation),
            )
            timer.daemon = True
            timer.start()

            def progress(stage_id: str, status: str, attempt: int = 0, detail: str = "") -> None:
                self._update_stage(
                    job_id, generation, stage_id, status, attempt, detail
                )

            result = self._worker.run(
                record.request,
                progress,
                checkpoint_dir=checkpoint_dir,
                resume=resume,
            )
            with self._lock:
                record = self._record(job_id)
                if record.generation != generation:
                    return
                if record.timed_out:
                    return
                if record.cancel_requested:
                    self._finish_cancelled(record)
                else:
                    record.result = result
                    record.status = "succeeded"
                    record.resume = False
                    record.updated_at = self._timestamp()
                    for stage in record.stages:
                        if stage["status"] == "pending":
                            stage["status"] = "skipped"
                            stage["finished_at"] = record.updated_at
                self._persist_locked()
        except _AnalysisCancelled:
            with self._lock:
                record = self._record(job_id)
                if not record.timed_out:
                    self._finish_cancelled(record)
                    self._persist_locked()
        except _AnalysisSuperseded:
            return
        except Exception as error:  # The job owns error normalization for the HTTP adapter.
            with self._lock:
                record = self._record(job_id)
                if record.generation != generation:
                    return
                if record.timed_out:
                    return
                record.status = "failed"
                record.resume = True
                record.error = {
                    "message": str(error) or type(error).__name__,
                    "type": type(error).__name__,
                    "retryable": True,
                }
                record.updated_at = self._timestamp()
                self._fail_running_stages(record, str(error))
                self._persist_locked()
        finally:
            if timer is not None:
                timer.cancel()

    def _update_stage(self, job_id: str, generation: int, stage_id: str, status: str, attempt: int, detail: str) -> None:
        allowed = {"running", "completed", "failed", "skipped", "retrying"}
        if status not in allowed:
            raise AnalysisJobError(f"unsupported job progress status: {status}")
        with self._lock:
            record = self._record(job_id)
            if record.generation != generation:
                raise _AnalysisSuperseded()
            if record.cancel_requested:
                raise _AnalysisCancelled()
            stage = next((item for item in record.stages if item["id"] == stage_id), None)
            if stage is None:
                return
            now = self._timestamp()
            stage["attempts"] = max(int(stage.get("attempts", 0)), int(attempt or 0))
            if detail:
                stage["detail"] = detail[:500]
            if status in {"running", "retrying"}:
                stage["status"] = status
                stage["started_at"] = stage["started_at"] or now
                stage["finished_at"] = None
            else:
                stage["status"] = status
                stage["started_at"] = stage["started_at"] or now
                stage["finished_at"] = now
            record.updated_at = now
            self._persist_locked()

    def _timeout_job(self, job_id: str, generation: int) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if (
                record is None
                or record.generation != generation
                or record.status not in {"queued", "running"}
            ):
                return
            record.timed_out = True
            record.cancel_requested = True
            record.resume = True
            record.status = "failed"
            record.updated_at = self._timestamp()
            record.error = {
                "message": f"分析任务超过 {self._timeout_seconds:g} 秒总时限，已停止接收结果。",
                "type": "AnalysisJobTimeout",
                "retryable": True,
            }
            self._fail_running_stages(record, record.error["message"])
            self._persist_locked()

    def _finish_cancelled(self, record: _JobRecord) -> None:
        record.status = "cancelled"
        record.resume = True
        record.updated_at = self._timestamp()
        for stage in record.stages:
            if stage["status"] in {"pending", "running", "retrying"}:
                stage["status"] = "cancelled"
                stage["finished_at"] = record.updated_at

    @staticmethod
    def _fail_running_stages(record: _JobRecord, detail: str) -> None:
        for stage in record.stages:
            if stage["status"] in {"running", "retrying"}:
                stage["status"] = "failed"
                stage["finished_at"] = record.updated_at
                stage["detail"] = detail[:500]

    def _record(self, job_id: str) -> _JobRecord:
        if not isinstance(job_id, str) or not job_id.strip():
            raise AnalysisJobError("缺少分析任务编号。")
        record = self._jobs.get(job_id.strip())
        if record is None:
            raise AnalysisJobError("分析任务不存在或已经过期。")
        return record

    def _evict_finished_jobs(self) -> None:
        while len(self._jobs) >= self._max_jobs:
            removable = next((job_id for job_id, record in self._jobs.items() if record.status in _TERMINAL), None)
            if removable is None:
                return
            del self._jobs[removable]

    def _snapshot(self, record: _JobRecord) -> dict[str, Any]:
        completed = sum(stage["status"] in {"completed", "skipped"} for stage in record.stages)
        current = next((stage["id"] for stage in record.stages if stage["status"] in {"running", "retrying"}), None)
        return {
            "job_id": record.job_id,
            "status": record.status,
            "request": {"symbol": record.request.symbol, "mode": record.request.mode},
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "cancel_requested": record.cancel_requested,
            "result_available": record.status == "succeeded",
            "error": deepcopy(record.error),
            "retry_count": record.retry_count,
            "recovered": record.recovered,
            "can_retry": record.status == "failed" and bool(record.error and record.error.get("retryable")),
            "timeout_seconds": self._timeout_seconds,
            "progress": {
                "current_stage": current,
                "completed": completed,
                "total": len(record.stages),
                "percent": round(completed / len(record.stages) * 100),
                "stages": deepcopy(record.stages),
            },
            "persistence": "json" if self._storage_path is not None else "memory_only",
        }

    @staticmethod
    def _new_stages() -> list[dict[str, Any]]:
        return [
            {
                "id": stage_id,
                "label": label,
                "group": group,
                "status": "pending",
                "attempts": 0,
                "detail": "",
                "started_at": None,
                "finished_at": None,
            }
            for stage_id, label, group in _STAGES
        ]

    def _checkpoint_dir(self, job_id: str, generation: int) -> Path | None:
        return (
            None
            if self._checkpoint_root is None
            else self._checkpoint_root / job_id / str(generation)
        )

    def _copy_checkpoint_generation(
        self, job_id: str, source_generation: int, target_generation: int
    ) -> None:
        source = self._checkpoint_dir(job_id, source_generation)
        target = self._checkpoint_dir(job_id, target_generation)
        if source is None or target is None or not source.exists():
            return
        try:
            shutil.copytree(source, target, dirs_exist_ok=True)
        except OSError as error:
            raise AnalysisJobError(f"无法准备任务重试检查点: {error}") from error

    def _load_and_resume(self) -> None:
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
            if payload.get("version") != 1 or not isinstance(payload.get("jobs"), list):
                raise AnalysisJobError("分析任务存储版本不受支持。")
            for item in payload["jobs"]:
                record = self._record_from_mapping(item)
                self._jobs[record.job_id] = record
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            if isinstance(error, AnalysisJobError):
                raise
            raise AnalysisJobError(f"无法恢复分析任务存储: {error}") from error
        recovered = []
        for record in self._jobs.values():
            if record.status in {"queued", "running"}:
                record.status = "queued"
                record.recovered = True
                record.resume = True
                record.cancel_requested = False
                record.error = None
                for stage in record.stages:
                    if stage["status"] in {"running", "retrying", "failed", "cancelled"}:
                        stage.update(status="pending", started_at=None, finished_at=None, detail="")
                recovered.append(record)
        self._persist_locked()
        for record in recovered:
            record.future = self._executor.submit(self._execute, record.job_id)

    def _record_from_mapping(self, value: Mapping[str, Any]) -> _JobRecord:
        result_value = value.get("result")
        result = None
        if isinstance(result_value, Mapping):
            result = ClientAnalysisResult(
                value=dict(result_value.get("value", {})),
                debate_context=result_value.get("debate_context"),
            )
        stages = value.get("stages")
        if not isinstance(stages, list):
            raise AnalysisJobError("分析任务存储缺少阶段状态。")
        return _JobRecord(
            job_id=str(value["job_id"]),
            request=ClientAnalysisRequest.from_mapping(value["request"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            status=str(value["status"]),
            stages=deepcopy(stages),
            result=result,
            error=deepcopy(value.get("error")),
            cancel_requested=bool(value.get("cancel_requested", False)),
            retry_count=int(value.get("retry_count", 0)),
            recovered=bool(value.get("recovered", False)),
            resume=bool(value.get("resume", False)),
            timed_out=bool(value.get("timed_out", False)),
            generation=int(value.get("generation", 0)),
        )

    def _persist_locked(self) -> None:
        if self._storage_path is None:
            return
        payload = {"version": 1, "jobs": [self._record_mapping(record) for record in self._jobs.values()]}
        temporary = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self._storage_path)
        except (OSError, TypeError, ValueError) as error:
            raise AnalysisJobError(f"无法保存分析任务状态: {error}") from error

    @staticmethod
    def _record_mapping(record: _JobRecord) -> dict[str, Any]:
        return {
            "job_id": record.job_id,
            "request": {"symbol": record.request.symbol, "mode": record.request.mode},
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "status": record.status,
            "stages": record.stages,
            "result": (
                None
                if record.result is None
                else {"value": record.result.to_mapping(), "debate_context": record.result.debate_context}
            ),
            "error": record.error,
            "cancel_requested": record.cancel_requested,
            "retry_count": record.retry_count,
            "recovered": record.recovered,
            "resume": record.resume,
            "timed_out": record.timed_out,
            "generation": record.generation,
        }

    def _timestamp(self) -> str:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise AnalysisJobError("analysis job clock must include a timezone")
        return value.isoformat(timespec="milliseconds")


__all__ = ["AnalysisJobError", "AnalysisJobRuntime", "AnalysisJobWorker", "ClientAnalysisJobWorker"]
