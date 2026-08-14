"""End-to-end observability for one customer financial analysis.

The module intentionally stores operational metadata only.  Prompts, API keys,
authorization headers, and complete market records are never accepted as trace
attributes.
"""

from __future__ import annotations

import json
import math
import re
from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol


class AnalysisObservabilityError(RuntimeError):
    """Trace data is invalid or cannot be persisted."""


_LAYERS = {"http", "task", "data", "graph", "harness", "model", "database"}
_STATUSES = {
    "queued", "running", "succeeded", "failed", "cancelled", "skipped",
    "retrying", "degraded", "cache_hit",
}
_FORBIDDEN_KEYS = {
    "api_key", "authorization", "cookie", "password", "prompt", "raw_input",
    "raw_output", "raw_records", "records", "secret", "system_prompt",
}
_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_ -]?key|authorization|password|secret|token)\b\s*[:=]\s*[^\s,;]+"
)


def safe_observation_text(value: Any, *, limit: int = 500) -> str:
    """Return bounded operational text with common credential forms redacted."""

    text = str(value or "")
    text = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text[:limit]


class AnalysisTraceStore(Protocol):
    def load(self) -> Mapping[str, Any]:
        """Load the complete trace collection."""

    def save(self, value: Mapping[str, Any]) -> None:
        """Atomically replace the trace collection."""


class InMemoryAnalysisTraceStore:
    def __init__(self) -> None:
        self._value: dict[str, Any] = {"version": 1, "traces": []}

    def load(self) -> Mapping[str, Any]:
        return deepcopy(self._value)

    def save(self, value: Mapping[str, Any]) -> None:
        self._value = deepcopy(dict(value))


class JsonAnalysisTraceStore:
    """Small local persistence adapter with atomic replacement."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    def load(self) -> Mapping[str, Any]:
        if not self.path.exists():
            return {"version": 1, "traces": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AnalysisObservabilityError(f"cannot read trace store: {error}") from error
        if not isinstance(value, Mapping):
            raise AnalysisObservabilityError("trace store root must be an object")
        return value

    def save(self, value: Mapping[str, Any]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.path)
        except (OSError, TypeError, ValueError) as error:
            raise AnalysisObservabilityError(f"cannot save trace store: {error}") from error


@dataclass(frozen=True)
class TraceSpan:
    layer: str
    component: str
    operation: str
    status: str
    started_at: str
    finished_at: str | None = None
    duration_ms: int | None = None
    attempts: int = 1
    detail: str = ""
    attributes: Mapping[str, Any] | None = None

    def to_mapping(self) -> dict[str, Any]:
        if self.layer not in _LAYERS:
            raise AnalysisObservabilityError(f"unsupported trace layer: {self.layer}")
        if self.status not in _STATUSES:
            raise AnalysisObservabilityError(f"unsupported trace status: {self.status}")
        if not self.component.strip() or not self.operation.strip():
            raise AnalysisObservabilityError("trace component and operation are required")
        _require_timestamp(self.started_at)
        if self.finished_at is not None:
            _require_timestamp(self.finished_at)
        duration = self.duration_ms
        if duration is None and self.finished_at is not None:
            duration = max(0, round((_parse_time(self.finished_at) - _parse_time(self.started_at)).total_seconds() * 1000))
        if duration is not None and duration < 0:
            raise AnalysisObservabilityError("trace duration cannot be negative")
        return {
            "layer": self.layer,
            "component": self.component.strip()[:100],
            "operation": self.operation.strip()[:100],
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": duration,
            "attempts": max(1, int(self.attempts)),
            "detail": safe_observation_text(self.detail),
            "attributes": _safe_attributes(self.attributes or {}),
        }


class AnalysisObservabilityRuntime:
    """One interface for trace capture, persistence, metrics, and projections."""

    VERSION = 1

    def __init__(self, store: AnalysisTraceStore, *, max_traces: int = 200) -> None:
        if isinstance(max_traces, bool) or not isinstance(max_traces, int) or max_traces < 1:
            raise ValueError("max_traces must be a positive integer")
        self._store = store
        self._max_traces = max_traces
        self._lock = RLock()
        self._traces: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._load()

    def begin(
        self,
        trace_id: str,
        *,
        job_id: str,
        request: Mapping[str, Any],
        timestamp: str,
    ) -> None:
        _require_identity(trace_id, "trace_id")
        _require_identity(job_id, "job_id")
        _require_timestamp(timestamp)
        safe_request = {
            key: str(request[key])[:80]
            for key in ("symbol", "mode")
            if key in request
        }
        with self._lock:
            current = self._traces.get(trace_id)
            if current is None:
                self._traces[trace_id] = {
                    "trace_id": trace_id,
                    "job_id": job_id,
                    "request": safe_request,
                    "status": "queued",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "error": None,
                    "spans": [],
                }
            else:
                current["updated_at"] = max(
                    (str(current.get("updated_at", timestamp)), timestamp),
                    key=_parse_time,
                )
            self._trim_and_save_locked()

    def span(self, trace_id: str, span: TraceSpan) -> None:
        mapping = span.to_mapping()
        with self._lock:
            trace = self._trace_locked(trace_id)
            key = (mapping["layer"], mapping["component"], mapping["operation"])
            existing = next(
                (
                    item for item in trace["spans"]
                    if (item["layer"], item["component"], item["operation"]) == key
                ),
                None,
            )
            if existing is None:
                mapping["sequence"] = len(trace["spans"]) + 1
                trace["spans"].append(mapping)
            else:
                sequence = existing["sequence"]
                existing.update(mapping)
                existing["sequence"] = sequence
            observed_at = mapping["finished_at"] or mapping["started_at"]
            trace["updated_at"] = max(
                (str(trace.get("updated_at", observed_at)), observed_at),
                key=_parse_time,
            )
            if mapping["status"] in {"running", "retrying"}:
                trace["status"] = "running"
            self._save_locked()

    def finish(
        self,
        trace_id: str,
        *,
        status: str,
        timestamp: str,
        error_type: str = "",
        error_message: str = "",
        user_action: str = "",
    ) -> None:
        if status not in {"succeeded", "failed", "cancelled"}:
            raise AnalysisObservabilityError("trace final status is invalid")
        _require_timestamp(timestamp)
        with self._lock:
            trace = self._trace_locked(trace_id)
            trace["status"] = status
            trace["updated_at"] = max(
                (str(trace.get("updated_at", timestamp)), timestamp), key=_parse_time
            )
            trace["error"] = (
                None
                if status == "succeeded"
                else {
                    "type": safe_observation_text(error_type, limit=100),
                    "message": safe_observation_text(error_message),
                    "user_action": safe_observation_text(user_action, limit=240),
                }
            )
            self._save_locked()

    def trace(self, trace_id: str) -> dict[str, Any]:
        with self._lock:
            trace = deepcopy(self._trace_locked(trace_id))
        return _trace_projection(trace)

    def overview(self, *, limit: int = 12) -> dict[str, Any]:
        with self._lock:
            traces = [deepcopy(item) for item in self._traces.values()]
        terminal = [item for item in traces if item["status"] in {"succeeded", "failed", "cancelled"}]
        successful = [item for item in terminal if item["status"] == "succeeded"]
        latencies = sorted(_trace_duration(item) for item in terminal)
        spans = [span for item in traces for span in item["spans"]]
        data_spans = [item for item in spans if item["layer"] == "data"]
        model_spans = [item for item in spans if item["layer"] == "model"]
        retries = [item for item in spans if int(item.get("attempts", 1)) > 1 or item["status"] == "retrying"]
        degraded = [item for item in data_spans if item["status"] == "degraded"]
        cache_hits = [item for item in data_spans if item["status"] == "cache_hit" or item.get("attributes", {}).get("cache_hit") is True]
        data_failures = [item for item in data_spans if item["status"] == "failed"]
        total_tokens = sum(int(item.get("attributes", {}).get("total_tokens", 0) or 0) for item in model_spans)
        completed_spans = [
            item for item in spans
            if item["layer"] in {"http", "task", "graph", "model", "database"}
            and isinstance(item.get("duration_ms"), int)
        ]
        slowest = sorted(completed_spans, key=lambda item: item["duration_ms"], reverse=True)[:5]
        recent = [_trace_summary(item) for item in reversed(traces[-max(1, int(limit)):])]
        return {
            "metrics": {
                "trace_count": len(traces),
                "completed_count": len(terminal),
                "success_rate_percent": _rate(len(successful), len(terminal)),
                "latency_p50_ms": _percentile(latencies, 50),
                "latency_p95_ms": _percentile(latencies, 95),
                "data_source_failure_rate_percent": _rate(len(data_failures), len(data_spans)),
                "cache_hit_rate_percent": _rate(len(cache_hits), len(data_spans)),
                "degradation_rate_percent": _rate(len(degraded), len(data_spans)),
                "retry_rate_percent": _rate(len(retries), len(spans)),
                "total_tokens": total_tokens,
            },
            "slowest": [_span_summary(item) for item in slowest],
            "recent_traces": recent,
            "storage": type(self._store).__name__,
        }

    def remove_job(self, job_id: str) -> bool:
        with self._lock:
            trace_id = next((key for key, item in self._traces.items() if item["job_id"] == job_id), None)
            if trace_id is None:
                return False
            del self._traces[trace_id]
            self._save_locked()
            return True

    def _trace_locked(self, trace_id: str) -> dict[str, Any]:
        _require_identity(trace_id, "trace_id")
        trace = self._traces.get(trace_id)
        if trace is None:
            raise AnalysisObservabilityError("analysis trace does not exist")
        return trace

    def _load(self) -> None:
        value = self._store.load()
        if value.get("version") != self.VERSION or not isinstance(value.get("traces"), list):
            raise AnalysisObservabilityError("trace store format is incompatible")
        for trace in value["traces"]:
            if not isinstance(trace, Mapping):
                raise AnalysisObservabilityError("trace store contains an invalid trace")
            trace_id = str(trace.get("trace_id", ""))
            _require_identity(trace_id, "trace_id")
            normalized = deepcopy(dict(trace))
            observed_times = [str(normalized.get("created_at", "")), str(normalized.get("updated_at", ""))]
            for span in normalized.get("spans", []):
                if isinstance(span, Mapping):
                    observed_times.extend(
                        str(item) for item in (span.get("started_at"), span.get("finished_at")) if item
                    )
            valid_times = []
            for item in observed_times:
                try:
                    valid_times.append(item)
                    _parse_time(item)
                except (TypeError, ValueError):
                    valid_times.pop()
            if valid_times:
                normalized["updated_at"] = max(valid_times, key=_parse_time)
            self._traces[trace_id] = normalized

    def _trim_and_save_locked(self) -> None:
        while len(self._traces) > self._max_traces:
            self._traces.popitem(last=False)
        self._save_locked()

    def _save_locked(self) -> None:
        self._store.save({"version": self.VERSION, "traces": list(self._traces.values())})


def _safe_attributes(value: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()[:80]
        normalized = key.lower()
        if normalized in _FORBIDDEN_KEYS or normalized.endswith("_api_key"):
            continue
        if isinstance(raw_value, bool) or raw_value is None:
            output[key] = raw_value
        elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            output[key] = raw_value
        elif isinstance(raw_value, str):
            output[key] = safe_observation_text(raw_value, limit=240)
    return output


def _trace_projection(trace: dict[str, Any]) -> dict[str, Any]:
    spans = sorted(trace["spans"], key=lambda item: (item["started_at"], item["sequence"]))
    output = deepcopy(trace)
    output["duration_ms"] = _trace_duration(trace)
    output["spans"] = [_span_summary(item) for item in spans]
    output["summary"] = {
        "span_count": len(spans),
        "failed_count": sum(item["status"] == "failed" for item in spans),
        "degraded_count": sum(item["status"] == "degraded" for item in spans),
        "retry_count": sum(max(0, int(item.get("attempts", 1)) - 1) for item in spans),
        "total_tokens": sum(int(item.get("attributes", {}).get("total_tokens", 0) or 0) for item in spans if item["layer"] == "model"),
    }
    return output


def _trace_summary(trace: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "trace_id": trace["trace_id"],
        "job_id": trace["job_id"],
        "request": deepcopy(trace.get("request", {})),
        "status": trace["status"],
        "created_at": trace["created_at"],
        "updated_at": trace["updated_at"],
        "duration_ms": _trace_duration(trace),
        "error_type": (trace.get("error") or {}).get("type", ""),
    }


def _span_summary(span: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sequence": span.get("sequence", 0),
        "layer": span["layer"],
        "component": span["component"],
        "operation": span["operation"],
        "status": span["status"],
        "started_at": span["started_at"],
        "finished_at": span.get("finished_at"),
        "duration_ms": span.get("duration_ms"),
        "attempts": span.get("attempts", 1),
        "detail": span.get("detail", ""),
        "attributes": deepcopy(span.get("attributes", {})),
    }


def _trace_duration(trace: Mapping[str, Any]) -> int:
    try:
        return max(0, round((_parse_time(str(trace["updated_at"])) - _parse_time(str(trace["created_at"]))).total_seconds() * 1000))
    except (KeyError, ValueError):
        return 0


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    index = max(0, math.ceil(percentile / 100 * len(values)) - 1)
    return int(values[index])


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed


def _require_timestamp(value: str) -> None:
    try:
        _parse_time(value)
    except (TypeError, ValueError) as error:
        raise AnalysisObservabilityError("trace timestamp must include timezone") from error


def _require_identity(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise AnalysisObservabilityError(f"{name} is invalid")


__all__ = [
    "AnalysisObservabilityError",
    "AnalysisObservabilityRuntime",
    "AnalysisTraceStore",
    "InMemoryAnalysisTraceStore",
    "JsonAnalysisTraceStore",
    "TraceSpan",
    "safe_observation_text",
]
