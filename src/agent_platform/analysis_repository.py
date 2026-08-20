"""Durable report history behind a small repository interface."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, Protocol
from uuid import uuid4


class AnalysisRepositoryError(RuntimeError):
    """Persistent analysis history is unavailable or inconsistent."""


@dataclass(frozen=True)
class AnalysisArchive:
    report_id: str
    report_version: int
    job_id: str
    symbol: str
    mode: str
    created_at: str
    archived_at: str
    task: Mapping[str, Any]
    result: Mapping[str, Any]
    debate_context: Mapping[str, Any] | None
    snapshot: Mapping[str, Any] | None
    agents: Mapping[str, Any]
    graphs: Mapping[str, Any]
    provenance: Mapping[str, Any] | None = None


class AnalysisRepository(Protocol):
    def archive(self, value: AnalysisArchive) -> str:
        """Atomically save one immutable report and all supporting evidence."""

    def list_reports(self, *, limit: int = 12) -> list[dict[str, Any]]:
        """Return newest report summaries."""

    def get_report(self, report_id: str) -> dict[str, Any]:
        """Return one verified historical report."""

    def record_model_call(self, report_id: str, value: Mapping[str, Any]) -> str:
        """Append non-sensitive model-call metadata for an archived report."""

    def record_model_feedback(self, report_id: str, value: Mapping[str, Any]) -> str:
        """Append helpful/not-helpful feedback for one archived explanation."""

    def delete_report(self, report_id: str) -> dict[str, Any]:
        """Delete one report and return the deleted report/job identity."""

    def clear_reports(self) -> list[dict[str, Any]]:
        """Delete all reports and return their report/job identities."""


_SENSITIVE_FIELDS = {
    "api_key", "apikey", "authorization", "password", "secret",
    "deepseek_api_key", "tushare_token", "access_token", "refresh_token",
}
_SECRET_VALUE = re.compile(r"(?i)(?:bearer\s+|sk-)[a-z0-9_-]{12,}")


def _json(value: Any) -> str:
    _assert_safe(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise AnalysisRepositoryError(f"历史记录包含无法序列化的数据: {error}") from error


def _assert_safe(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if (
                normalized in _SENSITIVE_FIELDS
                or normalized.endswith("_api_key")
                or (normalized.endswith("_token") and not normalized.endswith("_tokens"))
            ):
                raise AnalysisRepositoryError(f"拒绝保存敏感字段: {path}.{key}")
            _assert_safe(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_safe(item, f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        raise AnalysisRepositoryError(f"拒绝保存疑似密钥内容: {path}")


def _checksum(*parts: str) -> str:
    payload = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _summary_from_result(
    *, report_id: str, job_id: str, report_version: int, archived_at: str,
    result: Mapping[str, Any], task_status: str = "succeeded",
) -> dict[str, Any]:
    security = result.get("security", {})
    data = result.get("data", {})
    verdict = result.get("verdict", {})
    snapshot = data.get("snapshot") or {}
    datasets = snapshot.get("datasets", []) if isinstance(snapshot, Mapping) else []
    statuses = [
        str(item.get("status", "unknown"))
        for item in datasets
        if isinstance(item, Mapping)
    ]
    return {
        "report_id": report_id,
        "report_version": report_version,
        "job_id": job_id,
        "task_status": task_status,
        "symbol": security.get("symbol", ""),
        "name": security.get("name", ""),
        "code": security.get("code", ""),
        "mode": data.get("mode", ""),
        "data_label": data.get("label", ""),
        "as_of": data.get("as_of"),
        "snapshot_id": data.get("snapshot_id") or snapshot.get("snapshot_id"),
        "verdict": verdict.get("label", ""),
        "action": verdict.get("action_label", ""),
        "archived_at": archived_at,
        "data_health": {
            "available_count": snapshot.get("available_count"),
            "dataset_count": snapshot.get("dataset_count"),
            "degraded": bool(snapshot.get("degraded", False)),
            "unavailable_count": statuses.count("not_available"),
            "degraded_count": sum(
                status in {"backup", "cache_stale", "not_available"}
                for status in statuses
            ),
        },
        "data_quality": _quality_summary(result.get("provenance")),
    }


def _quality_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {
            "overall_status": "unknown",
            "comparison_ready": False,
            "note": "历史报告未保存数据质量与运行指纹。",
        }
    quality = value.get("quality")
    if not isinstance(quality, Mapping):
        return {
            "overall_status": "unknown",
            "comparison_ready": False,
            "note": "历史报告未保存数据质量与运行指纹。",
        }
    return {
        "overall_status": str(quality.get("overall_status", "unknown")),
        "comparison_ready": bool(quality.get("comparison_ready", False)),
        "note": str(quality.get("comparison_note", "")),
    }


class InMemoryAnalysisRepository:
    """Thread-safe test adapter with the same observable contract as SQLite."""

    def __init__(self) -> None:
        self._reports: dict[str, dict[str, Any]] = {}
        self._model_calls: dict[str, list[dict[str, Any]]] = {}
        self._model_feedback: dict[str, list[dict[str, Any]]] = {}
        self._lock = RLock()

    def archive(self, value: AnalysisArchive) -> str:
        _assert_safe(value.__dict__)
        with self._lock:
            existing = self._reports.get(value.report_id)
            payload = deepcopy(value.__dict__)
            if existing is not None and existing != payload:
                raise AnalysisRepositoryError("同一报告编号不能覆盖不同内容。")
            self._reports[value.report_id] = payload
        return value.report_id

    def list_reports(self, *, limit: int = 12) -> list[dict[str, Any]]:
        _validate_limit(limit)
        with self._lock:
            values = sorted(
                self._reports.values(), key=lambda item: item["archived_at"], reverse=True
            )[:limit]
            return [
                _summary_from_result(
                    report_id=item["report_id"], job_id=item["job_id"],
                    report_version=item["report_version"], archived_at=item["archived_at"],
                    result=item["result"], task_status=str(item["task"].get("status", "succeeded")),
                )
                for item in values
            ]

    def get_report(self, report_id: str) -> dict[str, Any]:
        report_id = _validate_id(report_id)
        with self._lock:
            value = self._reports.get(report_id)
            if value is None:
                raise AnalysisRepositoryError("历史报告不存在。")
            output = deepcopy(value)
            output["model_calls"] = deepcopy(self._model_calls.get(report_id, []))
            output["model_feedback"] = deepcopy(self._model_feedback.get(report_id, []))
            return output

    def record_model_call(self, report_id: str, value: Mapping[str, Any]) -> str:
        report_id = _validate_id(report_id)
        _assert_safe(value)
        call_id = uuid4().hex
        with self._lock:
            if report_id not in self._reports:
                raise AnalysisRepositoryError("历史报告不存在。")
            self._model_calls.setdefault(report_id, []).append({"call_id": call_id, **deepcopy(dict(value))})
        return call_id

    def record_model_feedback(self, report_id: str, value: Mapping[str, Any]) -> str:
        report_id = _validate_id(report_id)
        _validate_feedback(value)
        feedback_id = uuid4().hex
        with self._lock:
            if report_id not in self._reports:
                raise AnalysisRepositoryError("历史报告不存在。")
            self._model_feedback.setdefault(report_id, []).append(
                {"feedback_id": feedback_id, **deepcopy(dict(value))}
            )
        return feedback_id

    def delete_report(self, report_id: str) -> dict[str, Any]:
        report_id = _validate_id(report_id)
        with self._lock:
            value = self._reports.pop(report_id, None)
            if value is None:
                raise AnalysisRepositoryError("历史报告不存在。")
            self._model_calls.pop(report_id, None)
            self._model_feedback.pop(report_id, None)
            return {"report_id": report_id, "job_id": value["job_id"]}

    def clear_reports(self) -> list[dict[str, Any]]:
        with self._lock:
            deleted = [
                {"report_id": report_id, "job_id": value["job_id"]}
                for report_id, value in self._reports.items()
            ]
            self._reports.clear()
            self._model_calls.clear()
            self._model_feedback.clear()
            return deleted


class SQLiteAnalysisRepository:
    """SQLite adapter with versioned migrations and atomic report archives."""

    SCHEMA_VERSION = 4

    def __init__(self, path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        self.path = Path(path).resolve()
        self.timeout_seconds = float(timeout_seconds)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._session() as connection:
                self._migrate(connection)
        except (OSError, sqlite3.Error) as error:
            raise AnalysisRepositoryError(f"无法初始化分析历史数据库: {error}") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, timeout=self.timeout_seconds, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {max(1, int(self.timeout_seconds * 1000))}")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _migrate(self, connection: sqlite3.Connection) -> None:
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current > self.SCHEMA_VERSION:
            raise AnalysisRepositoryError(
                f"数据库版本 {current} 高于程序支持版本 {self.SCHEMA_VERSION}。"
            )
        if current < 1:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE analysis_reports (
                    report_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    report_version INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    debate_context_json TEXT NOT NULL,
                    checksum TEXT NOT NULL
                );
                CREATE TABLE analysis_tasks (
                    job_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL REFERENCES analysis_reports(report_id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE analysis_snapshots (
                    report_id TEXT PRIMARY KEY REFERENCES analysis_reports(report_id) ON DELETE CASCADE,
                    snapshot_id TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE analysis_agents (
                    report_id TEXT NOT NULL REFERENCES analysis_reports(report_id) ON DELETE CASCADE,
                    agent_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (report_id, agent_name)
                );
                CREATE TABLE analysis_graphs (
                    report_id TEXT NOT NULL REFERENCES analysis_reports(report_id) ON DELETE CASCADE,
                    graph_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (report_id, graph_name)
                );
                CREATE TABLE model_calls (
                    call_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL REFERENCES analysis_reports(report_id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    usage_json TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX analysis_reports_archived_at_idx ON analysis_reports(archived_at DESC);
                PRAGMA user_version = 1;
                COMMIT;
                """
            )
            current = 1
        if current < 2:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE model_calls ADD COLUMN kind TEXT NOT NULL DEFAULT 'unknown';
                ALTER TABLE model_calls ADD COLUMN output_json TEXT NOT NULL DEFAULT '{}';
                PRAGMA user_version = 2;
                COMMIT;
                """
            )
            current = 2
        if current < 3:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE model_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL REFERENCES analysis_reports(report_id) ON DELETE CASCADE,
                    rating TEXT NOT NULL CHECK (rating IN ('helpful', 'not_helpful')),
                    explanation_version TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX model_feedback_report_idx ON model_feedback(report_id, created_at);
                PRAGMA user_version = 3;
                COMMIT;
                """
            )
            current = 3
        if current < 4:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE analysis_provenance (
                    report_id TEXT PRIMARY KEY REFERENCES analysis_reports(report_id) ON DELETE CASCADE,
                    quality_json TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    fingerprint TEXT
                );
                PRAGMA user_version = 4;
                COMMIT;
                """
            )

    def archive(self, value: AnalysisArchive) -> str:
        _assert_safe(value.__dict__)
        result_json = _json(value.result)
        context_json = _json(value.debate_context or {})
        snapshot_json = _json(value.snapshot or {})
        task_json = _json(value.task)
        agents_json = _json(value.agents)
        graphs_json = _json(value.graphs)
        provenance_json = _json(value.provenance or {})
        checksum = _checksum(
            result_json, context_json, snapshot_json, task_json, agents_json, graphs_json,
            provenance_json,
        )
        try:
            with self._session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT checksum FROM analysis_reports WHERE report_id = ?", (value.report_id,)
                ).fetchone()
                if existing is not None:
                    if existing["checksum"] != checksum:
                        raise AnalysisRepositoryError("同一报告编号不能覆盖不同内容。")
                    connection.execute("COMMIT")
                    return value.report_id
                connection.execute(
                    "INSERT INTO analysis_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (value.report_id, value.job_id, value.report_version, value.symbol, value.mode,
                     value.created_at, value.archived_at, result_json, context_json, checksum),
                )
                connection.execute(
                    "INSERT INTO analysis_tasks VALUES (?, ?, ?, ?)",
                    (value.job_id, value.report_id, str(value.task.get("status", "succeeded")), task_json),
                )
                snapshot_id = (value.snapshot or {}).get("snapshot_id")
                connection.execute(
                    "INSERT INTO analysis_snapshots VALUES (?, ?, ?)",
                    (value.report_id, snapshot_id, snapshot_json),
                )
                connection.executemany(
                    "INSERT INTO analysis_agents VALUES (?, ?, ?)",
                    [(value.report_id, str(name), _json(payload)) for name, payload in value.agents.items()],
                )
                connection.executemany(
                    "INSERT INTO analysis_graphs VALUES (?, ?, ?)",
                    [(value.report_id, str(name), _json(payload)) for name, payload in value.graphs.items()],
                )
                if value.provenance is not None:
                    quality = value.provenance.get("quality", {})
                    identity = value.provenance.get("identity", {})
                    connection.execute(
                        "INSERT INTO analysis_provenance VALUES (?, ?, ?, ?)",
                        (
                            value.report_id,
                            _json(quality),
                            _json(identity),
                            value.provenance.get("fingerprint"),
                        ),
                    )
                connection.execute("COMMIT")
                return value.report_id
        except AnalysisRepositoryError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise AnalysisRepositoryError(f"无法完整保存分析历史: {error}") from error

    def list_reports(self, *, limit: int = 12) -> list[dict[str, Any]]:
        _validate_limit(limit)
        try:
            with self._session() as connection:
                rows = connection.execute(
                    """SELECT r.report_id, r.job_id, r.report_version, r.archived_at,
                              r.result_json, t.status
                       FROM analysis_reports r JOIN analysis_tasks t ON t.report_id = r.report_id
                       ORDER BY r.archived_at DESC, r.report_id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [
                _summary_from_result(
                    report_id=row["report_id"], job_id=row["job_id"],
                    report_version=row["report_version"], archived_at=row["archived_at"],
                    result=self._decode(row["result_json"], "report"), task_status=row["status"],
                )
                for row in rows
            ]
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as error:
            if isinstance(error, AnalysisRepositoryError):
                raise
            raise AnalysisRepositoryError(f"无法读取分析历史列表: {error}") from error

    def get_report(self, report_id: str) -> dict[str, Any]:
        report_id = _validate_id(report_id)
        try:
            with self._session() as connection:
                row = connection.execute(
                    """SELECT r.*, t.status, t.payload_json, s.payload_json AS snapshot_json,
                              p.quality_json AS provenance_quality_json,
                              p.identity_json AS provenance_identity_json,
                              p.fingerprint AS provenance_fingerprint
                       FROM analysis_reports r
                       JOIN analysis_tasks t ON t.report_id = r.report_id
                       JOIN analysis_snapshots s ON s.report_id = r.report_id
                       LEFT JOIN analysis_provenance p ON p.report_id = r.report_id
                       WHERE r.report_id = ?""",
                    (report_id,),
                ).fetchone()
                if row is None:
                    raise AnalysisRepositoryError("历史报告不存在。")
                agents = {
                    item["agent_name"]: self._decode(item["payload_json"], "agent")
                    for item in connection.execute(
                        "SELECT agent_name, payload_json FROM analysis_agents WHERE report_id = ?", (report_id,)
                    )
                }
                graphs = {
                    item["graph_name"]: self._decode(item["payload_json"], "graph")
                    for item in connection.execute(
                        "SELECT graph_name, payload_json FROM analysis_graphs WHERE report_id = ?", (report_id,)
                    )
                }
                calls = [
                    {
                        "call_id": item["call_id"], "provider": item["provider"], "model": item["model"],
                        "status": item["status"], "usage": self._decode(item["usage_json"], "model usage"),
                        "latency_ms": item["latency_ms"], "created_at": item["created_at"],
                        "kind": item["kind"], "output": self._decode(item["output_json"], "model output"),
                    }
                    for item in connection.execute(
                        "SELECT * FROM model_calls WHERE report_id = ? ORDER BY created_at, call_id", (report_id,)
                    )
                ]
                feedback = [
                    {
                        "feedback_id": item["feedback_id"],
                        "rating": item["rating"],
                        "explanation_version": item["explanation_version"],
                        "provider": item["provider"],
                        "model": item["model"],
                        "created_at": item["created_at"],
                        "metadata": self._decode(item["metadata_json"], "feedback metadata"),
                    }
                    for item in connection.execute(
                        "SELECT * FROM model_feedback WHERE report_id = ? ORDER BY created_at, feedback_id",
                        (report_id,),
                    )
                ]
            result = self._decode(row["result_json"], "report")
            context = self._decode(row["debate_context_json"], "debate context")
            snapshot = self._decode(row["snapshot_json"], "snapshot")
            provenance = None
            if row["provenance_quality_json"] is not None:
                provenance = {
                    "schema_version": 1,
                    "quality": self._decode(
                        row["provenance_quality_json"], "provenance quality"
                    ),
                    "identity": self._decode(
                        row["provenance_identity_json"], "provenance identity"
                    ),
                    "fingerprint": row["provenance_fingerprint"],
                }
            provenance_json = _json(provenance or {})
            checksum = _checksum(
                row["result_json"], row["debate_context_json"], row["snapshot_json"],
                row["payload_json"], _json(agents), _json(graphs), provenance_json,
            )
            legacy_checksum = _checksum(
                row["result_json"], row["debate_context_json"], row["snapshot_json"],
                row["payload_json"], _json(agents), _json(graphs),
            )
            if row["checksum"] not in {checksum, legacy_checksum}:
                raise AnalysisRepositoryError("历史报告完整性校验失败，数据可能已损坏。")
            return {
                "report_id": row["report_id"], "report_version": row["report_version"],
                "job_id": row["job_id"], "symbol": row["symbol"], "mode": row["mode"],
                "created_at": row["created_at"], "archived_at": row["archived_at"],
                "task": self._decode(row["payload_json"], "task"), "result": result,
                "debate_context": context or None, "snapshot": snapshot or None,
                "agents": agents, "graphs": graphs, "provenance": provenance,
                "model_calls": calls,
                "model_feedback": feedback,
            }
        except AnalysisRepositoryError:
            raise
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AnalysisRepositoryError(f"无法读取历史报告: {error}") from error

    def record_model_call(self, report_id: str, value: Mapping[str, Any]) -> str:
        report_id = _validate_id(report_id)
        _assert_safe(value)
        call_id = uuid4().hex
        usage = value.get("usage", {})
        try:
            with self._session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute(
                    "SELECT 1 FROM analysis_reports WHERE report_id = ?", (report_id,)
                ).fetchone() is None:
                    raise AnalysisRepositoryError("历史报告不存在。")
                connection.execute(
                    """INSERT INTO model_calls
                       (call_id, report_id, provider, model, status, usage_json,
                        latency_ms, created_at, kind, output_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (call_id, report_id, str(value.get("provider", "unknown")),
                     str(value.get("model", "unknown")), str(value.get("status", "succeeded")),
                     _json(usage), int(value.get("latency_ms", 0)), str(value.get("created_at", "")),
                     str(value.get("kind", "unknown")), _json(value.get("output", {}))),
                )
                connection.execute("COMMIT")
            return call_id
        except AnalysisRepositoryError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise AnalysisRepositoryError(f"无法保存模型调用记录: {error}") from error

    def record_model_feedback(self, report_id: str, value: Mapping[str, Any]) -> str:
        report_id = _validate_id(report_id)
        _validate_feedback(value)
        feedback_id = uuid4().hex
        try:
            with self._session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute(
                    "SELECT 1 FROM analysis_reports WHERE report_id = ?", (report_id,)
                ).fetchone() is None:
                    raise AnalysisRepositoryError("历史报告不存在。")
                connection.execute(
                    """INSERT INTO model_feedback
                       (feedback_id, report_id, rating, explanation_version,
                        provider, model, created_at, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        feedback_id,
                        report_id,
                        str(value["rating"]),
                        str(value.get("explanation_version", "unknown")),
                        str(value.get("provider", "unknown")),
                        str(value.get("model", "unknown")),
                        str(value.get("created_at", "")),
                        _json(value.get("metadata", {})),
                    ),
                )
                connection.execute("COMMIT")
            return feedback_id
        except AnalysisRepositoryError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise AnalysisRepositoryError(f"无法保存模型反馈: {error}") from error

    def delete_report(self, report_id: str) -> dict[str, Any]:
        report_id = _validate_id(report_id)
        try:
            with self._session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT job_id FROM analysis_reports WHERE report_id = ?", (report_id,)
                ).fetchone()
                if row is None:
                    raise AnalysisRepositoryError("历史报告不存在。")
                connection.execute(
                    "DELETE FROM analysis_reports WHERE report_id = ?", (report_id,)
                )
                connection.execute("COMMIT")
                return {"report_id": report_id, "job_id": row["job_id"]}
        except AnalysisRepositoryError:
            raise
        except sqlite3.Error as error:
            raise AnalysisRepositoryError(f"无法删除历史报告: {error}") from error

    def clear_reports(self) -> list[dict[str, Any]]:
        try:
            with self._session() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    "SELECT report_id, job_id FROM analysis_reports ORDER BY archived_at"
                ).fetchall()
                connection.execute("DELETE FROM analysis_reports")
                connection.execute("COMMIT")
                return [
                    {"report_id": row["report_id"], "job_id": row["job_id"]}
                    for row in rows
                ]
        except sqlite3.Error as error:
            raise AnalysisRepositoryError(f"无法清空分析历史: {error}") from error

    @staticmethod
    def _decode(value: str, label: str) -> dict[str, Any]:
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise AnalysisRepositoryError(f"历史 {label} 不是对象。")
        _assert_safe(decoded)
        return decoded


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise AnalysisRepositoryError("历史报告数量必须在 1 到 100 之间。")


def _validate_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise AnalysisRepositoryError("历史报告编号无效。")
    return value.strip()


def _validate_feedback(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise AnalysisRepositoryError("模型反馈必须是对象。")
    rating = value.get("rating")
    if rating not in {"helpful", "not_helpful"}:
        raise AnalysisRepositoryError("模型反馈必须是 helpful 或 not_helpful。")
    _assert_safe(value)


__all__ = [
    "AnalysisArchive", "AnalysisRepository", "AnalysisRepositoryError",
    "InMemoryAnalysisRepository", "SQLiteAnalysisRepository",
]
