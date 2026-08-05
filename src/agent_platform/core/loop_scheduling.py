"""Persistent heartbeat, cron, hook, and recursive-goal loop orchestration."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from .contracts import AgentRequest
from .task_workspace import TaskWorkspace, TaskWorkspaceManager


class LoopSchedulingError(ValueError):
    """A scheduled-loop contract or configuration is invalid."""


class LoopRunSnapshotError(RuntimeError):
    """A loop-run ledger snapshot is corrupted or incompatible."""


class GoalLoopLimitError(RuntimeError):
    """A recursive goal loop exceeded its depth or goal limit."""


class LoopRunStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class LoopTaskResult:
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise LoopSchedulingError("loop task result content must be a string")
        if not isinstance(self.metadata, Mapping):
            raise LoopSchedulingError("loop task result metadata must be an object")
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(_normalize_json_object(self.metadata)),
        )


@dataclass(frozen=True)
class LoopInvocation:
    run_id: str
    task_id: str
    request: AgentRequest
    trigger: str
    dedupe_key: str
    workspace: TaskWorkspace
    depth: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id", 128))
        object.__setattr__(
            self,
            "task_id",
            _required_text(self.task_id, "task_id", 128),
        )
        if not isinstance(self.request, AgentRequest):
            raise LoopSchedulingError("loop invocation request must be AgentRequest")
        object.__setattr__(
            self,
            "trigger",
            _required_text(self.trigger, "trigger", 128),
        )
        object.__setattr__(
            self,
            "dedupe_key",
            _required_text(self.dedupe_key, "dedupe_key", 1_000),
        )
        if not isinstance(self.workspace, TaskWorkspace):
            raise LoopSchedulingError("loop invocation workspace must be TaskWorkspace")
        _validate_non_negative_int(self.depth, "invocation depth")


@dataclass(frozen=True)
class LoopRunRecord:
    run_id: str
    dedupe_key: str
    task_id: str
    task: str
    trigger: str
    status: LoopRunStatus
    started_at: datetime
    finished_at: datetime
    workspace: str
    depth: int = 0
    output: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.run_id, "run_id", 128),
            (self.dedupe_key, "dedupe_key", 1_000),
            (self.task_id, "task_id", 128),
            (self.task, "task", 10_000),
            (self.trigger, "trigger", 128),
            (self.workspace, "workspace", 2_000),
        ):
            _required_text(value, name, maximum)
        try:
            status = LoopRunStatus(self.status)
        except (TypeError, ValueError) as error:
            raise LoopSchedulingError("loop run status is unsupported") from error
        started_at = _aware_datetime(self.started_at, "started_at")
        finished_at = _aware_datetime(self.finished_at, "finished_at")
        if finished_at < started_at:
            raise LoopSchedulingError("finished_at cannot be earlier than started_at")
        _validate_non_negative_int(self.depth, "record depth")
        if not isinstance(self.output, str) or not isinstance(self.error, str):
            raise LoopSchedulingError("loop output and error must be strings")
        if status is LoopRunStatus.COMPLETED and self.error:
            raise LoopSchedulingError("completed loop run cannot contain an error")
        if status is LoopRunStatus.FAILED and not self.error:
            raise LoopSchedulingError("failed loop run must contain an error")
        if not isinstance(self.metadata, Mapping):
            raise LoopSchedulingError("loop run metadata must be an object")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(_normalize_json_object(self.metadata)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dedupe_key": self.dedupe_key,
            "task_id": self.task_id,
            "task": self.task,
            "trigger": self.trigger,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "workspace": self.workspace,
            "depth": self.depth,
            "output": self.output,
            "metadata": _json_copy(dict(self.metadata)),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LoopRunRecord":
        expected = {
            "run_id",
            "dedupe_key",
            "task_id",
            "task",
            "trigger",
            "status",
            "started_at",
            "finished_at",
            "workspace",
            "depth",
            "output",
            "metadata",
            "error",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise LoopSchedulingError(
                "loop run record fields do not match version 1 contract"
            )
        try:
            started_at = datetime.fromisoformat(payload["started_at"])
            finished_at = datetime.fromisoformat(payload["finished_at"])
        except (TypeError, ValueError) as error:
            raise LoopSchedulingError(
                "loop run timestamps must be ISO 8601 strings"
            ) from error
        return cls(
            run_id=payload["run_id"],
            dedupe_key=payload["dedupe_key"],
            task_id=payload["task_id"],
            task=payload["task"],
            trigger=payload["trigger"],
            status=payload["status"],
            started_at=started_at,
            finished_at=finished_at,
            workspace=payload["workspace"],
            depth=payload["depth"],
            output=payload["output"],
            metadata=payload["metadata"],
            error=payload["error"],
        )


@dataclass(frozen=True)
class LoopRunSnapshot:
    version: int
    records: tuple[LoopRunRecord, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version != 1
        ):
            raise LoopRunSnapshotError(
                f"unsupported loop run snapshot version: {self.version}"
            )
        records = tuple(self.records)
        if any(not isinstance(record, LoopRunRecord) for record in records):
            raise LoopRunSnapshotError(
                "loop run snapshot must contain LoopRunRecord values"
            )
        run_ids = tuple(record.run_id for record in records)
        dedupe_keys = tuple(record.dedupe_key for record in records)
        if len(run_ids) != len(set(run_ids)) or len(dedupe_keys) != len(
            set(dedupe_keys)
        ):
            raise LoopRunSnapshotError(
                "loop run snapshot contains duplicate run or dedupe ids"
            )
        object.__setattr__(self, "records", records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "records": [record.to_dict() for record in self.records],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LoopRunSnapshot":
        if not isinstance(payload, Mapping) or set(payload) != {"version", "records"}:
            raise LoopRunSnapshotError(
                "loop run snapshot fields do not match version 1 contract"
            )
        records_raw = payload["records"]
        if not isinstance(records_raw, list):
            raise LoopRunSnapshotError("loop run records must be an array")
        try:
            return cls(
                version=payload["version"],
                records=tuple(
                    LoopRunRecord.from_dict(item) for item in records_raw
                ),
            )
        except LoopRunSnapshotError:
            raise
        except (TypeError, ValueError) as error:
            raise LoopRunSnapshotError(
                f"loop run snapshot is invalid: {error}"
            ) from error


class LoopRunStore(Protocol):
    def save(self, snapshot: LoopRunSnapshot) -> None:
        """Persist all loop run records."""

    def load(self) -> LoopRunSnapshot | None:
        """Load the latest run ledger snapshot."""


class LoopRunLedger:
    """Persistent idempotency and audit ledger shared by all loop triggers."""

    def __init__(self, store: LoopRunStore) -> None:
        if not callable(getattr(store, "save", None)) or not callable(
            getattr(store, "load", None)
        ):
            raise LoopSchedulingError("loop run store must provide save and load")
        self._store = store
        snapshot = store.load()
        records = () if snapshot is None else snapshot.records
        self._records = {record.dedupe_key: record for record in records}

    @property
    def records(self) -> tuple[LoopRunRecord, ...]:
        return tuple(self._records.values())

    def get(self, dedupe_key: str) -> LoopRunRecord | None:
        return self._records.get(_required_text(dedupe_key, "dedupe_key", 1_000))

    def record(self, item: LoopRunRecord) -> None:
        if not isinstance(item, LoopRunRecord):
            raise LoopSchedulingError("ledger requires LoopRunRecord")
        existing = self._records.get(item.dedupe_key)
        if existing is not None and existing != item:
            raise LoopSchedulingError(
                f"dedupe key already belongs to another run: {item.dedupe_key}"
            )
        self._records[item.dedupe_key] = item
        self._store.save(LoopRunSnapshot(version=1, records=self.records))


class InMemoryLoopRunStore:
    def __init__(self) -> None:
        self._snapshot: LoopRunSnapshot | None = None

    def save(self, snapshot: LoopRunSnapshot) -> None:
        if not isinstance(snapshot, LoopRunSnapshot):
            raise LoopRunSnapshotError("run store requires LoopRunSnapshot")
        self._snapshot = LoopRunSnapshot.from_dict(snapshot.to_dict())

    def load(self) -> LoopRunSnapshot | None:
        if self._snapshot is None:
            return None
        return LoopRunSnapshot.from_dict(self._snapshot.to_dict())


class JsonLoopRunStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, snapshot: LoopRunSnapshot) -> None:
        if not isinstance(snapshot, LoopRunSnapshot):
            raise LoopRunSnapshotError("run store requires LoopRunSnapshot")
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        except (OSError, TypeError, ValueError) as error:
            raise LoopRunSnapshotError(
                f"failed to save loop run snapshot: {error}"
            ) from error

    def load(self) -> LoopRunSnapshot | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return LoopRunSnapshot.from_dict(payload)
        except LoopRunSnapshotError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise LoopRunSnapshotError(
                f"failed to load loop run snapshot: {error}"
            ) from error


class LoopDispatcher:
    """Give every run an isolated workspace and persist its observable result."""

    def __init__(
        self,
        *,
        handler: Callable[[LoopInvocation], LoopTaskResult],
        workspace_manager: TaskWorkspaceManager,
        ledger: LoopRunLedger,
        clock: Callable[[], datetime],
    ) -> None:
        if not callable(handler):
            raise LoopSchedulingError("loop handler must be callable")
        if not isinstance(workspace_manager, TaskWorkspaceManager):
            raise LoopSchedulingError(
                "workspace_manager must be TaskWorkspaceManager"
            )
        if not isinstance(ledger, LoopRunLedger):
            raise LoopSchedulingError("ledger must be LoopRunLedger")
        if not callable(clock):
            raise LoopSchedulingError("dispatcher clock must be callable")
        self._handler = handler
        self._workspace_manager = workspace_manager
        self._ledger = ledger
        self._clock = clock

    @property
    def ledger(self) -> LoopRunLedger:
        return self._ledger

    def dispatch(
        self,
        *,
        task_id: str,
        request: AgentRequest,
        trigger: str,
        dedupe_key: str,
        depth: int = 0,
    ) -> LoopRunRecord:
        normalized_dedupe = _required_text(dedupe_key, "dedupe_key", 1_000)
        existing = self._ledger.get(normalized_dedupe)
        if existing is not None:
            return existing
        run_id = hashlib.sha256(normalized_dedupe.encode("utf-8")).hexdigest()[:16]
        workspace = self._workspace_manager.open(f"run-{run_id}")
        invocation = LoopInvocation(
            run_id=run_id,
            task_id=task_id,
            request=request,
            trigger=trigger,
            dedupe_key=normalized_dedupe,
            workspace=workspace,
            depth=depth,
        )
        started_at = _aware_datetime(self._clock(), "dispatcher clock result")
        status = LoopRunStatus.COMPLETED
        output = ""
        metadata: Mapping[str, Any] = {}
        error_text = ""
        try:
            result = self._handler(invocation)
            if not isinstance(result, LoopTaskResult):
                raise LoopSchedulingError(
                    "loop handler must return LoopTaskResult"
                )
            output = result.content
            metadata = result.metadata
        except Exception as error:  # noqa: BLE001 - failures become audit records
            status = LoopRunStatus.FAILED
            error_text = f"{type(error).__name__}: {error}"
        finished_at = _aware_datetime(self._clock(), "dispatcher clock result")
        record = LoopRunRecord(
            run_id=run_id,
            dedupe_key=normalized_dedupe,
            task_id=task_id,
            task=request.task,
            trigger=trigger,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            workspace=str(workspace.path),
            depth=depth,
            output=output,
            metadata=metadata,
            error=error_text,
        )
        self._ledger.record(record)
        return record


class HeartbeatLoop:
    def __init__(
        self,
        dispatcher: LoopDispatcher,
        *,
        interval_seconds: int,
        anchor: datetime,
    ) -> None:
        if not isinstance(dispatcher, LoopDispatcher):
            raise LoopSchedulingError("dispatcher must be LoopDispatcher")
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, int)
            or interval_seconds < 1
        ):
            raise LoopSchedulingError(
                "heartbeat interval_seconds must be a positive integer"
            )
        self._dispatcher = dispatcher
        self._interval_seconds = interval_seconds
        self._anchor = _aware_datetime(anchor, "heartbeat anchor")

    def tick(
        self,
        *,
        task_id: str,
        request: AgentRequest,
        now: datetime,
    ) -> LoopRunRecord | None:
        current = _aware_datetime(now, "heartbeat now")
        elapsed = (current - self._anchor).total_seconds()
        if elapsed < 0:
            return None
        slot = int(elapsed // self._interval_seconds)
        dedupe_key = (
            f"heartbeat:{task_id}:{self._anchor.isoformat()}:"
            f"{self._interval_seconds}:{slot}"
        )
        return self._dispatcher.dispatch(
            task_id=task_id,
            request=request,
            trigger="heartbeat",
            dedupe_key=dedupe_key,
        )


class CronExpression:
    """Deterministic five-field cron expression with ranges, lists, and steps."""

    def __init__(self, expression: str) -> None:
        self.expression = _required_text(expression, "cron expression", 256)
        parts = self.expression.split()
        if len(parts) != 5:
            raise LoopSchedulingError(
                "cron expression must contain five fields"
            )
        self._minutes, self._minute_wildcard = _parse_cron_field(parts[0], 0, 59)
        self._hours, self._hour_wildcard = _parse_cron_field(parts[1], 0, 23)
        self._days, self._day_wildcard = _parse_cron_field(parts[2], 1, 31)
        self._months, self._month_wildcard = _parse_cron_field(parts[3], 1, 12)
        weekdays, self._weekday_wildcard = _parse_cron_field(parts[4], 0, 7)
        self._weekdays = frozenset(0 if value == 7 else value for value in weekdays)

    def matches(self, value: datetime) -> bool:
        moment = _aware_datetime(value, "cron moment")
        cron_weekday = (moment.weekday() + 1) % 7
        day_matches = moment.day in self._days
        weekday_matches = cron_weekday in self._weekdays
        if self._day_wildcard and self._weekday_wildcard:
            calendar_day_matches = True
        elif self._day_wildcard:
            calendar_day_matches = weekday_matches
        elif self._weekday_wildcard:
            calendar_day_matches = day_matches
        else:
            calendar_day_matches = day_matches or weekday_matches
        return (
            moment.minute in self._minutes
            and moment.hour in self._hours
            and moment.month in self._months
            and calendar_day_matches
        )


class CronLoop:
    def __init__(self, dispatcher: LoopDispatcher, *, expression: CronExpression) -> None:
        if not isinstance(dispatcher, LoopDispatcher):
            raise LoopSchedulingError("dispatcher must be LoopDispatcher")
        if not isinstance(expression, CronExpression):
            raise LoopSchedulingError("expression must be CronExpression")
        self._dispatcher = dispatcher
        self._expression = expression

    def tick(
        self,
        *,
        task_id: str,
        request: AgentRequest,
        now: datetime,
    ) -> LoopRunRecord | None:
        current = _aware_datetime(now, "cron now")
        if not self._expression.matches(current):
            return None
        minute = current.replace(second=0, microsecond=0)
        return self._dispatcher.dispatch(
            task_id=task_id,
            request=request,
            trigger="cron",
            dedupe_key=(
                f"cron:{task_id}:{self._expression.expression}:"
                f"{minute.isoformat()}"
            ),
        )


@dataclass(frozen=True)
class HookEvent:
    event_id: str
    name: str
    payload: Mapping[str, Any]
    occurred_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_id",
            _required_text(self.event_id, "hook event_id", 256),
        )
        object.__setattr__(
            self,
            "name",
            _required_text(self.name, "hook event name", 256),
        )
        if not isinstance(self.payload, Mapping):
            raise LoopSchedulingError("hook payload must be an object")
        object.__setattr__(
            self,
            "payload",
            MappingProxyType(_normalize_json_object(self.payload)),
        )
        object.__setattr__(
            self,
            "occurred_at",
            _aware_datetime(self.occurred_at, "hook occurred_at"),
        )

    def to_context(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "name": self.name,
            "payload": _json_copy(dict(self.payload)),
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass(frozen=True)
class HookSubscription:
    hook_id: str
    event_name: str
    task_id: str
    task: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.hook_id, "hook_id", 128),
            (self.event_name, "event_name", 256),
            (self.task_id, "task_id", 128),
            (self.task, "task", 10_000),
        ):
            _required_text(value, name, maximum)
        if not isinstance(self.context, Mapping):
            raise LoopSchedulingError("hook context must be an object")
        object.__setattr__(
            self,
            "context",
            MappingProxyType(_normalize_json_object(self.context)),
        )


class HookLoop:
    def __init__(
        self,
        dispatcher: LoopDispatcher,
        *,
        subscriptions: Sequence[HookSubscription],
    ) -> None:
        if not isinstance(dispatcher, LoopDispatcher):
            raise LoopSchedulingError("dispatcher must be LoopDispatcher")
        subscriptions = tuple(subscriptions)
        if any(not isinstance(item, HookSubscription) for item in subscriptions):
            raise LoopSchedulingError(
                "subscriptions must contain HookSubscription values"
            )
        hook_ids = tuple(item.hook_id for item in subscriptions)
        if len(hook_ids) != len(set(hook_ids)):
            raise LoopSchedulingError("hook subscription ids must be unique")
        self._dispatcher = dispatcher
        self._subscriptions = subscriptions

    def emit(self, event: HookEvent) -> tuple[LoopRunRecord, ...]:
        if not isinstance(event, HookEvent):
            raise LoopSchedulingError("emit requires HookEvent")
        records = []
        for subscription in self._subscriptions:
            if subscription.event_name != event.name:
                continue
            context = dict(subscription.context)
            context["hook_event"] = event.to_context()
            records.append(
                self._dispatcher.dispatch(
                    task_id=subscription.task_id,
                    request=AgentRequest(task=subscription.task, context=context),
                    trigger="hook",
                    dedupe_key=(
                        f"hook:{subscription.hook_id}:{event.event_id}"
                    ),
                )
            )
        return tuple(records)


@dataclass(frozen=True)
class GoalLoopResult:
    run_id: str
    records: tuple[LoopRunRecord, ...]


class GoalLoop:
    def __init__(
        self,
        dispatcher: LoopDispatcher,
        *,
        max_depth: int,
        max_goals: int,
    ) -> None:
        if not isinstance(dispatcher, LoopDispatcher):
            raise LoopSchedulingError("dispatcher must be LoopDispatcher")
        _validate_non_negative_int(max_depth, "max_depth")
        if isinstance(max_goals, bool) or not isinstance(max_goals, int) or max_goals < 1:
            raise LoopSchedulingError("max_goals must be a positive integer")
        self._dispatcher = dispatcher
        self._max_depth = max_depth
        self._max_goals = max_goals

    def run(
        self,
        *,
        run_id: str,
        root_goal: str,
        decompose: Callable[[str, int], Sequence[str]],
        context: Mapping[str, Any] | None = None,
    ) -> GoalLoopResult:
        normalized_run_id = _required_text(run_id, "goal run_id", 128)
        normalized_goal = _required_text(root_goal, "root goal", 10_000)
        if not callable(decompose):
            raise LoopSchedulingError("goal decomposer must be callable")
        base_context = {} if context is None else _normalize_json_object(context)
        records: list[LoopRunRecord] = []
        visited = 0

        def visit(goal: str, depth: int, path: str) -> LoopRunRecord:
            nonlocal visited
            visited += 1
            if visited > self._max_goals:
                raise GoalLoopLimitError(
                    f"goal loop exceeded max_goals={self._max_goals}"
                )
            children_raw = decompose(goal, depth)
            if isinstance(children_raw, (str, bytes)) or not isinstance(
                children_raw,
                Sequence,
            ):
                raise LoopSchedulingError(
                    "goal decomposer must return a sequence of goal strings"
                )
            children = tuple(
                _required_text(item, "subgoal", 10_000) for item in children_raw
            )
            if children and depth >= self._max_depth:
                raise GoalLoopLimitError(
                    f"goal loop exceeded max_depth={self._max_depth} at {path}"
                )
            child_records = [
                visit(child, depth + 1, f"{path}.{index}")
                for index, child in enumerate(children, start=1)
            ]
            request_context = dict(base_context)
            request_context["goal_context"] = {
                "run_id": normalized_run_id,
                "path": path,
                "depth": depth,
                "child_results": [
                    {
                        "task": item.task,
                        "status": item.status.value,
                        "output": item.output,
                        "error": item.error,
                    }
                    for item in child_records
                ],
            }
            goal_digest = hashlib.sha256(goal.encode("utf-8")).hexdigest()[:12]
            record = self._dispatcher.dispatch(
                task_id=f"goal-{normalized_run_id}-{path}",
                request=AgentRequest(task=goal, context=request_context),
                trigger="goal",
                dedupe_key=(
                    f"goal:{normalized_run_id}:{path}:{goal_digest}"
                ),
                depth=depth,
            )
            records.append(record)
            return record

        visit(normalized_goal, 0, "root")
        return GoalLoopResult(run_id=normalized_run_id, records=tuple(records))


class CognitiveLoopTaskHandler:
    """Adapter that executes scheduled invocations through CognitiveLoopRunner."""

    def __init__(self, runner_factory: Callable[[TaskWorkspace], Any]) -> None:
        if not callable(runner_factory):
            raise LoopSchedulingError("runner_factory must be callable")
        self._runner_factory = runner_factory

    def __call__(self, invocation: LoopInvocation) -> LoopTaskResult:
        if not isinstance(invocation, LoopInvocation):
            raise LoopSchedulingError(
                "cognitive loop handler requires LoopInvocation"
            )
        from .cognitive_loop import CognitiveLoopRunner

        runner = self._runner_factory(invocation.workspace)
        if not isinstance(runner, CognitiveLoopRunner):
            raise LoopSchedulingError(
                "runner_factory must return CognitiveLoopRunner"
            )
        result = runner.run(invocation.request)
        return LoopTaskResult(
            content=result.response.content,
            metadata={
                "steps": result.state.step_count,
                "working_memory_entries": len(result.state.memory.entries),
                "project_memory_entries": len(result.state.context.project_memory),
                "organization_memory_entries": len(
                    result.state.context.organization_memory
                ),
                "workspace": str(invocation.workspace.path),
            },
        )


def _parse_cron_field(
    text: str,
    minimum: int,
    maximum: int,
) -> tuple[frozenset[int], bool]:
    if not isinstance(text, str) or not text:
        raise LoopSchedulingError("cron field cannot be empty")
    values: set[int] = set()
    wildcard = text == "*"
    for part in text.split(","):
        if not part:
            raise LoopSchedulingError("cron field contains an empty list item")
        base, separator, step_text = part.partition("/")
        step = 1
        if separator:
            if not step_text.isdigit() or int(step_text) < 1:
                raise LoopSchedulingError("cron step must be a positive integer")
            step = int(step_text)
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise LoopSchedulingError("cron range must contain integers")
            start, end = int(start_text), int(end_text)
        else:
            if separator:
                raise LoopSchedulingError(
                    "cron step requires '*' or a numeric range"
                )
            if not base.isdigit():
                raise LoopSchedulingError("cron value must be an integer")
            start = end = int(base)
        if start < minimum or end > maximum or start > end:
            raise LoopSchedulingError(
                f"cron value must be between {minimum} and {maximum}"
            )
        values.update(range(start, end + 1, step))
    return frozenset(values), wildcard


def _required_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LoopSchedulingError(f"{name} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum:
        raise LoopSchedulingError(f"{name} must not exceed {maximum} characters")
    return text


def _aware_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LoopSchedulingError(f"{name} must be a timezone-aware datetime")
    return value


def _validate_non_negative_int(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LoopSchedulingError(f"{name} must be a non-negative integer")


def _normalize_json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LoopSchedulingError("value must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise LoopSchedulingError("JSON object keys must be strings")
    try:
        return _json_copy(dict(value))
    except (TypeError, ValueError) as error:
        raise LoopSchedulingError(
            f"value must be JSON-compatible: {error}"
        ) from error


def _json_copy(value: Any) -> Any:
    _reject_non_finite_numbers(value)
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _reject_non_finite_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numbers are not supported")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("object keys must be strings")
            _reject_non_finite_numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite_numbers(item)
