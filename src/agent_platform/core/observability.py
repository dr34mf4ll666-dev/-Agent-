"""统一归一化 Harness、Graph 与 Model Gateway 的可观测记录。"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from .contracts import HarnessExecutionError, HarnessResult
from .graph import GraphExecutionError, GraphResult
from .model_gateway import ModelGatewayExecutionError, ModelGatewayResult


class ObservabilityContractError(ValueError):
    """可观测记录缺少必要信息或违反聚合契约。"""


@dataclass(frozen=True)
class ObservedEvent:
    """来自不同运行层、已经归一化的有序事件。"""

    sequence: int
    event: str
    status: str
    component: str
    attempt: int = 0
    detail: str = ""

    def to_mapping(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event": self.event,
            "status": self.status,
            "component": self.component,
            "attempt": self.attempt,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ObservationRecord:
    """一次 Harness、Graph 或 Model Gateway 执行的统一记录。"""

    run_id: str
    layer: str
    component: str
    status: str
    started_at: datetime
    duration_ms: int
    events: tuple[ObservedEvent, ...]
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    error_type: str = ""
    error_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ObservabilityContractError("run_id must not be blank")
        if self.layer not in {"harness", "graph", "model"}:
            raise ObservabilityContractError(f"unsupported observation layer: {self.layer}")
        if not self.component.strip():
            raise ObservabilityContractError("component must not be blank")
        if self.status not in {"succeeded", "failed"}:
            raise ObservabilityContractError(f"unsupported run status: {self.status}")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ObservabilityContractError("started_at must be timezone-aware")
        if self.duration_ms < 0:
            raise ObservabilityContractError("duration_ms must be >= 0")
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ObservabilityContractError("token counts must be >= 0")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ObservabilityContractError(
                "total_tokens must equal input_tokens + output_tokens"
            )
        if self.status == "failed" and not self.error_message:
            raise ObservabilityContractError("failed records must preserve error_message")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "layer": self.layer,
            "component": self.component,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "duration_ms": self.duration_ms,
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "total": self.total_tokens,
            },
            "error": {
                "type": self.error_type,
                "message": self.error_message,
            }
            if self.error_message
            else None,
            "metadata": deepcopy(self.metadata),
            "call_chain": [event.to_mapping() for event in self.events],
        }


@dataclass(frozen=True)
class ObservabilityReport:
    """多次运行的汇总指标和逐次调用链。"""

    summary: dict[str, Any]
    by_layer: dict[str, dict[str, Any]]
    records: tuple[ObservationRecord, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "summary": deepcopy(self.summary),
            "by_layer": deepcopy(self.by_layer),
            "runs": [record.to_mapping() for record in self.records],
        }


class ObservationAdapter:
    """把三种既有运行结果转换为同一个稳定契约。"""

    @staticmethod
    def from_execution(
        *,
        run_id: str,
        execution: (
            HarnessResult
            | HarnessExecutionError
            | GraphResult
            | GraphExecutionError
            | ModelGatewayResult
            | ModelGatewayExecutionError
        ),
        started_at: datetime,
        duration_ms: int | None = None,
        component: str | None = None,
    ) -> ObservationRecord:
        if isinstance(execution, (HarnessResult, HarnessExecutionError)):
            return _from_harness(
                run_id, execution, started_at, duration_ms, component
            )
        if isinstance(execution, (GraphResult, GraphExecutionError)):
            return _from_graph(run_id, execution, started_at, duration_ms, component)
        if isinstance(execution, (ModelGatewayResult, ModelGatewayExecutionError)):
            return _from_model(run_id, execution, started_at, duration_ms, component)
        raise ObservabilityContractError(
            f"unsupported execution type: {type(execution).__name__}"
        )


class ObservabilityDashboard:
    """只依赖统一记录计算面板指标，不侵入 Agent 业务代码。"""

    @staticmethod
    def build(records: Sequence[ObservationRecord]) -> ObservabilityReport:
        records = tuple(records)
        run_ids = [record.run_id for record in records]
        if len(run_ids) != len(set(run_ids)):
            raise ObservabilityContractError("run_id values must be unique")

        succeeded = sum(record.status == "succeeded" for record in records)
        failed = len(records) - succeeded
        durations = [record.duration_ms for record in records]
        summary = _metric_summary(records, succeeded, failed, durations)

        by_layer: dict[str, dict[str, Any]] = {}
        for layer in ("harness", "graph", "model"):
            layer_records = tuple(record for record in records if record.layer == layer)
            if not layer_records:
                continue
            layer_succeeded = sum(
                record.status == "succeeded" for record in layer_records
            )
            layer_failed = len(layer_records) - layer_succeeded
            by_layer[layer] = _metric_summary(
                layer_records,
                layer_succeeded,
                layer_failed,
                [record.duration_ms for record in layer_records],
            )

        return ObservabilityReport(
            summary=summary,
            by_layer=by_layer,
            records=records,
        )


def _from_harness(
    run_id: str,
    execution: HarnessResult | HarnessExecutionError,
    started_at: datetime,
    duration_ms: int | None,
    component: str | None,
) -> ObservationRecord:
    duration = _required_duration(duration_ms, "harness")
    trace = execution.trace
    agent = component or (trace[0].agent if trace else "unknown_agent")
    failed = isinstance(execution, HarnessExecutionError)
    cause = execution.cause if failed else None
    return ObservationRecord(
        run_id=run_id,
        layer="harness",
        component=agent,
        status="failed" if failed else "succeeded",
        started_at=started_at,
        duration_ms=duration,
        events=tuple(
            ObservedEvent(
                sequence=index,
                event=event.event,
                status=_event_status(event.event),
                component=event.agent,
                detail=event.detail,
            )
            for index, event in enumerate(trace, start=1)
        ),
        error_type=type(cause).__name__ if cause else "",
        error_message=str(cause) if cause else "",
        metadata={"agent": agent, "event_timing": "ordered_only"},
    )


def _from_graph(
    run_id: str,
    execution: GraphResult | GraphExecutionError,
    started_at: datetime,
    duration_ms: int | None,
    component: str | None,
) -> ObservationRecord:
    duration = _required_duration(duration_ms, "graph")
    graph_name = component or "graph"
    failed = isinstance(execution, GraphExecutionError)
    cause = execution.cause if failed else None
    return ObservationRecord(
        run_id=run_id,
        layer="graph",
        component=graph_name,
        status="failed" if failed else "succeeded",
        started_at=started_at,
        duration_ms=duration,
        events=tuple(
            ObservedEvent(
                sequence=index,
                event=event.event,
                status=_event_status(event.event),
                component=event.node or graph_name,
                attempt=event.attempt,
                detail=event.detail,
            )
            for index, event in enumerate(execution.trace, start=1)
        ),
        error_type=type(cause).__name__ if cause else "",
        error_message=str(cause) if cause else "",
        metadata={
            "statuses": deepcopy(execution.statuses),
            "execution_order": list(execution.execution_order),
            "attempts": deepcopy(execution.attempts),
            "circuit_breakers": deepcopy(execution.circuit_breakers),
            "event_timing": "ordered_only",
        },
    )


def _from_model(
    run_id: str,
    execution: ModelGatewayResult | ModelGatewayExecutionError,
    started_at: datetime,
    duration_ms: int | None,
    component: str | None,
) -> ObservationRecord:
    failed = isinstance(execution, ModelGatewayExecutionError)
    trace = execution.trace
    provider = trace[0].provider if trace else "unknown_provider"
    model = trace[0].model if trace else "unknown_model"
    name = component or f"{provider}/{model}"
    if failed:
        duration = _required_duration(duration_ms, "model")
        usage = (0, 0, 0)
        cause = execution.cause
        attempts = execution.attempts
        error_code = execution.code.value
    else:
        duration = execution.response.latency_ms if duration_ms is None else duration_ms
        usage = (
            execution.response.usage.input_tokens,
            execution.response.usage.output_tokens,
            execution.response.usage.total_tokens,
        )
        cause = None
        attempts = execution.response.attempts
        error_code = ""

    return ObservationRecord(
        run_id=run_id,
        layer="model",
        component=name,
        status="failed" if failed else "succeeded",
        started_at=started_at,
        duration_ms=duration,
        events=tuple(
            ObservedEvent(
                sequence=index,
                event=event.event,
                status=_event_status(event.event),
                component=f"{event.provider}/{event.model}",
                attempt=event.attempt,
                detail=event.detail,
            )
            for index, event in enumerate(trace, start=1)
        ),
        input_tokens=usage[0],
        output_tokens=usage[1],
        total_tokens=usage[2],
        error_type=type(cause).__name__ if cause else "",
        error_message=str(cause) if cause else "",
        metadata={
            "provider": provider,
            "model": model,
            "attempts": attempts,
            "error_code": error_code,
            "event_timing": "ordered_only",
        },
    )


def _required_duration(duration_ms: int | None, layer: str) -> int:
    if duration_ms is None:
        raise ObservabilityContractError(
            f"duration_ms is required for {layer} observations"
        )
    return duration_ms


def _event_status(event: str) -> str:
    if any(word in event for word in ("failed", "timeout", "blocked", "opened")):
        return "failed"
    if "retry" in event:
        return "retrying"
    if "skipped" in event:
        return "skipped"
    if event.endswith(".started"):
        return "started"
    if any(
        event.endswith(suffix)
        for suffix in (".passed", ".finished", ".succeeded", ".completed")
    ):
        return "succeeded"
    return "info"


def _metric_summary(
    records: Sequence[ObservationRecord],
    succeeded: int,
    failed: int,
    durations: Sequence[int],
) -> dict[str, Any]:
    total = len(records)
    sorted_durations = sorted(durations)
    p95_index = max(0, math.ceil(len(sorted_durations) * 0.95) - 1)
    return {
        "total_runs": total,
        "succeeded_runs": succeeded,
        "failed_runs": failed,
        "failure_rate_percent": round(failed / total * 100, 2) if total else 0.0,
        "tokens": {
            "input": sum(record.input_tokens for record in records),
            "output": sum(record.output_tokens for record in records),
            "total": sum(record.total_tokens for record in records),
        },
        "latency_ms": {
            "total": sum(durations),
            "average": round(sum(durations) / total, 2) if total else 0.0,
            "p95": sorted_durations[p95_index] if sorted_durations else 0,
            "maximum": max(sorted_durations, default=0),
        },
    }
