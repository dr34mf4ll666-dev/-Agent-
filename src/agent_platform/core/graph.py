"""Deterministic DAG runtime with schemas, parallel waves and resilience."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .checkpoint import GraphCheckpoint, GraphCheckpointError, JsonCheckpointStore
from .contracts import AgentRequest, GuardrailConfigurationError, GuardrailViolation
from .guardrails import JSONSchemaValidator


NodeHandler = Callable[["GraphState"], Mapping[str, Any]]
EdgeCondition = Callable[["GraphState"], bool]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]
EventSink = Callable[["GraphEvent"], None]


@dataclass(frozen=True)
class GraphState:
    """Read-only shallow state snapshot passed to graph nodes."""

    values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def with_updates(self, updates: Mapping[str, Any]) -> "GraphState":
        merged = dict(self.values)
        merged.update(updates)
        return GraphState(values=merged)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.values)


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    """Open a node circuit after consecutive failures."""

    failure_threshold: int = 3
    reset_timeout_seconds: float | None = 60.0

    def __post_init__(self) -> None:
        if isinstance(self.failure_threshold, bool) or self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if (
            self.reset_timeout_seconds is not None
            and self.reset_timeout_seconds < 0
        ):
            raise ValueError("reset_timeout_seconds must not be negative")


@dataclass(frozen=True)
class NodeExecutionPolicy:
    """Retry, timeout and circuit-breaker policy for one node."""

    max_retries: int = 0
    retry_delay_seconds: float = 0.0
    timeout_seconds: float | None = None
    circuit_breaker: CircuitBreakerPolicy | None = None

    def __post_init__(self) -> None:
        if isinstance(self.max_retries, bool) or self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")


@dataclass(frozen=True)
class GraphExecutionPolicy:
    """Global scheduling policy for ready graph nodes."""

    strategy: str = "sequential"
    max_workers: int = 4

    def __post_init__(self) -> None:
        if self.strategy not in {"sequential", "parallel"}:
            raise ValueError("graph execution strategy must be sequential or parallel")
        if isinstance(self.max_workers, bool) or self.max_workers < 1:
            raise ValueError("max_workers must be at least 1")


@dataclass(frozen=True)
class GraphEdge:
    """Directed state-transfer edge with optional condition and two schemas."""

    source: str
    target: str
    condition: EdgeCondition | None = None
    output_schema: Mapping[str, Any] | None = None
    input_schema: Mapping[str, Any] | None = None
    condition_label: str = ""

    def __post_init__(self) -> None:
        if self.output_schema is not None:
            object.__setattr__(self, "output_schema", dict(self.output_schema))
        if self.input_schema is not None:
            object.__setattr__(self, "input_schema", dict(self.input_schema))


@dataclass(frozen=True)
class GraphDefinition:
    """Static graph definition and execution policy."""

    start: str
    nodes: Mapping[str, NodeHandler]
    edges: tuple[GraphEdge, ...] = ()
    node_policies: Mapping[str, NodeExecutionPolicy] = field(default_factory=dict)
    execution: GraphExecutionPolicy = field(default_factory=GraphExecutionPolicy)

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", MappingProxyType(dict(self.nodes)))
        object.__setattr__(self, "edges", tuple(self.edges))
        object.__setattr__(
            self,
            "node_policies",
            MappingProxyType(dict(self.node_policies)),
        )


@dataclass(frozen=True)
class GraphEvent:
    event: str
    node: str = ""
    attempt: int = 0
    detail: str = ""


class _EventBuffer(list[GraphEvent]):
    """Collect node events while forwarding them to an optional live observer."""

    def __init__(self, sink: EventSink | None = None) -> None:
        super().__init__()
        self._sink = sink

    def append(self, event: GraphEvent) -> None:
        super().append(event)
        if self._sink is not None:
            self._sink(event)


@dataclass(frozen=True)
class GraphResult:
    """Successful graph state and auditable runtime metadata."""

    state: GraphState
    statuses: dict[str, str]
    execution_order: tuple[str, ...]
    attempts: dict[str, int] = field(default_factory=dict)
    circuit_breakers: dict[str, dict[str, Any]] = field(default_factory=dict)
    trace: tuple[GraphEvent, ...] = ()


class GraphValidationError(ValueError):
    """The graph definition is not a valid executable DAG."""


class GraphContractError(TypeError):
    """A node or state transfer violated the graph contract."""


class GraphSchemaError(GraphContractError):
    """An edge rejected source updates or target input state."""


class GraphMergeConflictError(GraphContractError):
    """Parallel nodes attempted to update the same state key."""


class GraphNodeTimeoutError(TimeoutError):
    """A node exceeded its configured in-process soft timeout."""


class GraphCircuitOpenError(RuntimeError):
    """A node circuit is open and execution is blocked."""


class GraphExecutionError(RuntimeError):
    """A failed graph with state, policies and trace preserved."""

    def __init__(
        self,
        message: str,
        state: GraphState,
        statuses: dict[str, str],
        execution_order: tuple[str, ...],
        cause: Exception,
        *,
        attempts: dict[str, int] | None = None,
        circuit_breakers: dict[str, dict[str, Any]] | None = None,
        trace: tuple[GraphEvent, ...] = (),
    ) -> None:
        super().__init__(message)
        self.state = state
        self.statuses = statuses
        self.execution_order = execution_order
        self.cause = cause
        self.attempts = dict(attempts or {})
        self.circuit_breakers = _copy_breakers(circuit_breakers or {})
        self.trace = trace


@dataclass(frozen=True)
class _NodeOutcome:
    name: str
    updates: Mapping[str, Any] | None
    attempts: int
    breaker: dict[str, Any]
    events: tuple[GraphEvent, ...]
    error: Exception | None = None


def _closed_breaker() -> dict[str, Any]:
    return {"state": "closed", "failures": 0, "opened_at": None}


def _copy_breakers(
    breakers: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {name: dict(value) for name, value in breakers.items()}


class GraphRunner:
    """Validate and run a DAG through one small deterministic interface."""

    def __init__(
        self,
        graph: GraphDefinition,
        *,
        checkpoint_store: JsonCheckpointStore | None = None,
        clock: Clock = time.time,
        sleeper: Sleeper = time.sleep,
        event_sink: EventSink | None = None,
    ) -> None:
        self._graph = graph
        self._checkpoint_store = checkpoint_store
        self._clock = clock
        self._sleeper = sleeper
        self._event_sink = event_sink

    def _append_trace(self, trace: list[GraphEvent], event: GraphEvent) -> None:
        trace.append(event)
        if self._event_sink is not None:
            self._event_sink(event)

    def validate(self) -> tuple[str, ...]:
        """Validate structure, policies and schemas; return topological order."""

        return self._topological_order()

    def run(
        self,
        initial_state: Mapping[str, Any] | GraphState | None = None,
        *,
        resume: bool = False,
    ) -> GraphResult:
        topological_order = self.validate()
        signature = self._graph_signature()
        incoming, outgoing = self._edge_indexes()

        if resume:
            checkpoint = self._load_checkpoint(signature)
            state = GraphState(values=checkpoint.state)
            statuses = dict(checkpoint.statuses)
            edge_decisions = {
                int(index): selected
                for index, selected in checkpoint.edge_decisions.items()
            }
            execution_order = list(checkpoint.execution_order)
            attempts = {
                name: int(checkpoint.attempts.get(name, 0))
                for name in self._graph.nodes
            }
            circuit_breakers = {
                name: dict(
                    checkpoint.circuit_breakers.get(name, _closed_breaker())
                )
                for name in self._graph.nodes
            }
        else:
            state = (
                initial_state
                if isinstance(initial_state, GraphState)
                else GraphState(values=dict(initial_state or {}))
            )
            statuses = {name: "pending" for name in self._graph.nodes}
            edge_decisions: dict[int, bool] = {}
            execution_order: list[str] = []
            attempts = {name: 0 for name in self._graph.nodes}
            circuit_breakers = {
                name: _closed_breaker() for name in self._graph.nodes
            }

        trace: list[GraphEvent] = []
        self._append_trace(trace, GraphEvent(event="graph.started"))

        while any(status not in {"completed", "skipped"} for status in statuses.values()):
            skipped = self._propagate_skips(
                topological_order,
                statuses,
                edge_decisions,
                incoming,
                outgoing,
            )
            if skipped:
                for name in skipped:
                    self._append_trace(
                        trace, GraphEvent(event="graph.node.skipped", node=name)
                    )
                self._save_checkpoint(
                    signature,
                    state,
                    statuses,
                    edge_decisions,
                    execution_order,
                    attempts,
                    circuit_breakers,
                )

            ready = self._ready_nodes(
                topological_order,
                statuses,
                edge_decisions,
                incoming,
            )
            if not ready:
                if all(
                    status in {"completed", "skipped"}
                    for status in statuses.values()
                ):
                    break
                cause = GraphValidationError(
                    "graph has pending nodes but no executable node"
                )
                self._raise_execution_error(
                    "graph scheduler cannot make progress",
                    cause,
                    state,
                    statuses,
                    execution_order,
                    attempts,
                    circuit_breakers,
                    trace,
                )

            if self._graph.execution.strategy == "sequential":
                ready = ready[:1]

            self._append_trace(
                trace,
                GraphEvent(
                    event="graph.wave.started",
                    detail=",".join(ready),
                )
            )

            for node_name in ready:
                try:
                    self._validate_target_input(
                        node_name,
                        state,
                        incoming[node_name],
                        edge_decisions,
                    )
                except Exception as error:
                    statuses[node_name] = "failed"
                    self._append_trace(
                        trace,
                        GraphEvent(
                            event="graph.node.failed",
                            node=node_name,
                            detail=str(error),
                        )
                    )
                    self._save_checkpoint(
                        signature,
                        state,
                        statuses,
                        edge_decisions,
                        execution_order,
                        attempts,
                        circuit_breakers,
                    )
                    self._raise_execution_error(
                        f"graph failed at node {node_name}: {error}",
                        error,
                        state,
                        statuses,
                        execution_order,
                        attempts,
                        circuit_breakers,
                        trace,
                    )

            outcomes = self._run_wave(
                ready,
                state,
                attempts,
                circuit_breakers,
            )
            for name in ready:
                outcome = outcomes[name]
                attempts[name] += outcome.attempts
                circuit_breakers[name] = dict(outcome.breaker)
                trace.extend(outcome.events)

            valid_outcomes: dict[str, _NodeOutcome] = {}
            failures: dict[str, Exception] = {}
            decisions_by_node: dict[str, dict[int, bool]] = {}

            for name in ready:
                outcome = outcomes[name]
                if outcome.error is not None:
                    failures[name] = outcome.error
                    continue
                try:
                    assert outcome.updates is not None
                    decisions_by_node[name] = self._validate_output_and_decide(
                        name,
                        outcome.updates,
                        state,
                        outgoing[name],
                    )
                    valid_outcomes[name] = outcome
                except Exception as error:
                    failures[name] = error
                    self._append_trace(
                        trace,
                        GraphEvent(
                            event="graph.node.failed",
                            node=name,
                            detail=str(error),
                        )
                    )

            try:
                merged_updates = self._merge_parallel_updates(valid_outcomes, ready)
            except GraphMergeConflictError as error:
                for name in valid_outcomes:
                    failures[name] = error
                    self._append_trace(
                        trace,
                        GraphEvent(
                            event="graph.node.failed",
                            node=name,
                            detail=str(error),
                        )
                    )
                valid_outcomes.clear()
                merged_updates = {}

            if merged_updates:
                state = state.with_updates(merged_updates)

            for name in ready:
                if name in failures:
                    statuses[name] = "failed"
                    continue
                statuses[name] = "completed"
                execution_order.append(name)
                edge_decisions.update(decisions_by_node[name])

            self._save_checkpoint(
                signature,
                state,
                statuses,
                edge_decisions,
                execution_order,
                attempts,
                circuit_breakers,
            )

            if failures:
                failed_name = next(name for name in ready if name in failures)
                error = failures[failed_name]
                self._raise_execution_error(
                    f"graph failed at node {failed_name}: {error}",
                    error,
                    state,
                    statuses,
                    execution_order,
                    attempts,
                    circuit_breakers,
                    trace,
                )

        self._append_trace(trace, GraphEvent(event="graph.completed"))
        return GraphResult(
            state=state,
            statuses=dict(statuses),
            execution_order=tuple(execution_order),
            attempts=dict(attempts),
            circuit_breakers=_copy_breakers(circuit_breakers),
            trace=tuple(trace),
        )

    def _run_wave(
        self,
        ready: list[str],
        state: GraphState,
        attempts: Mapping[str, int],
        circuit_breakers: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, _NodeOutcome]:
        if self._graph.execution.strategy == "sequential" or len(ready) == 1:
            name = ready[0]
            return {
                name: self._execute_node(
                    name,
                    state,
                    attempts[name],
                    circuit_breakers[name],
                )
            }

        outcomes: dict[str, _NodeOutcome] = {}
        with ThreadPoolExecutor(
            max_workers=min(self._graph.execution.max_workers, len(ready)),
            thread_name_prefix="graph-node",
        ) as executor:
            futures = {
                name: executor.submit(
                    self._execute_node,
                    name,
                    state,
                    attempts[name],
                    circuit_breakers[name],
                )
                for name in ready
            }
            for name in ready:
                outcomes[name] = futures[name].result()
        return outcomes

    def _execute_node(
        self,
        name: str,
        state: GraphState,
        previous_attempts: int,
        breaker_snapshot: Mapping[str, Any],
    ) -> _NodeOutcome:
        policy = self._graph.node_policies.get(name, NodeExecutionPolicy())
        breaker = dict(breaker_snapshot)
        events: list[GraphEvent] = _EventBuffer(self._event_sink)

        if policy.circuit_breaker is not None and breaker["state"] == "open":
            reset_after = policy.circuit_breaker.reset_timeout_seconds
            opened_at = breaker.get("opened_at")
            can_probe = (
                reset_after is not None
                and opened_at is not None
                and self._clock() - float(opened_at) >= reset_after
            )
            if not can_probe:
                error = GraphCircuitOpenError(f"circuit is open for node {name}")
                events.append(
                    GraphEvent(event="graph.circuit.blocked", node=name, detail=str(error))
                )
                return _NodeOutcome(
                    name=name,
                    updates=None,
                    attempts=0,
                    breaker=breaker,
                    events=tuple(events),
                    error=error,
                )
            breaker = {"state": "half_open", "failures": 0, "opened_at": None}
            events.append(GraphEvent(event="graph.circuit.half_open", node=name))

        attempts_this_run = 0
        last_error: Exception | None = None

        for local_attempt in range(1, policy.max_retries + 2):
            attempts_this_run += 1
            absolute_attempt = previous_attempts + attempts_this_run
            events.append(
                GraphEvent(
                    event="graph.node.started",
                    node=name,
                    attempt=absolute_attempt,
                )
            )
            try:
                updates = self._call_with_timeout(
                    name,
                    self._graph.nodes[name],
                    state,
                    policy.timeout_seconds,
                )
                if not isinstance(updates, Mapping):
                    raise GraphContractError(
                        f"node {name} must return a mapping of state updates"
                    )
                breaker = _closed_breaker()
                events.append(
                    GraphEvent(
                        event="graph.node.completed",
                        node=name,
                        attempt=absolute_attempt,
                    )
                )
                return _NodeOutcome(
                    name=name,
                    updates=dict(updates),
                    attempts=attempts_this_run,
                    breaker=breaker,
                    events=tuple(events),
                )
            except Exception as error:
                last_error = error
                event_name = (
                    "graph.node.timeout"
                    if isinstance(error, GraphNodeTimeoutError)
                    else "graph.node.attempt_failed"
                )
                events.append(
                    GraphEvent(
                        event=event_name,
                        node=name,
                        attempt=absolute_attempt,
                        detail=str(error),
                    )
                )

                if policy.circuit_breaker is not None:
                    failures = int(breaker.get("failures", 0)) + 1
                    breaker = {
                        "state": breaker.get("state", "closed"),
                        "failures": failures,
                        "opened_at": breaker.get("opened_at"),
                    }
                    if failures >= policy.circuit_breaker.failure_threshold:
                        breaker = {
                            "state": "open",
                            "failures": failures,
                            "opened_at": self._clock(),
                        }
                        circuit_error = GraphCircuitOpenError(
                            f"circuit opened for node {name} after {failures} failures"
                        )
                        events.append(
                            GraphEvent(
                                event="graph.circuit.opened",
                                node=name,
                                attempt=absolute_attempt,
                                detail=str(error),
                            )
                        )
                        return _NodeOutcome(
                            name=name,
                            updates=None,
                            attempts=attempts_this_run,
                            breaker=breaker,
                            events=tuple(events),
                            error=circuit_error,
                        )

                if local_attempt <= policy.max_retries:
                    events.append(
                        GraphEvent(
                            event="graph.node.retry",
                            node=name,
                            attempt=absolute_attempt,
                            detail=str(error),
                        )
                    )
                    if policy.retry_delay_seconds:
                        self._sleeper(policy.retry_delay_seconds)
                    continue
                break

        assert last_error is not None
        events.append(
            GraphEvent(event="graph.node.failed", node=name, detail=str(last_error))
        )
        return _NodeOutcome(
            name=name,
            updates=None,
            attempts=attempts_this_run,
            breaker=breaker,
            events=tuple(events),
            error=last_error,
        )

    @staticmethod
    def _call_with_timeout(
        name: str,
        handler: NodeHandler,
        state: GraphState,
        timeout_seconds: float | None,
    ) -> Mapping[str, Any]:
        if timeout_seconds is None:
            return handler(state)

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"node-{name}")
        future = executor.submit(handler, state)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as error:
            future.cancel()
            raise GraphNodeTimeoutError(
                f"node {name} exceeded {timeout_seconds} seconds"
            ) from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _validate_output_and_decide(
        self,
        node_name: str,
        updates: Mapping[str, Any],
        state: GraphState,
        outgoing_indexes: list[int],
    ) -> dict[int, bool]:
        decisions: dict[int, bool] = {}
        candidate = state.with_updates(updates)
        for edge_index in outgoing_indexes:
            edge = self._graph.edges[edge_index]
            selected = (
                True if edge.condition is None else bool(edge.condition(candidate))
            )
            if selected and edge.output_schema is not None:
                self._validate_schema(
                    updates,
                    edge.output_schema,
                    edge,
                    stage="output",
                )
            decisions[edge_index] = selected
        return decisions

    def _validate_target_input(
        self,
        node_name: str,
        state: GraphState,
        incoming_indexes: list[int],
        edge_decisions: Mapping[int, bool],
    ) -> None:
        for edge_index in incoming_indexes:
            if not edge_decisions.get(edge_index, False):
                continue
            edge = self._graph.edges[edge_index]
            if edge.input_schema is not None:
                self._validate_schema(
                    state.to_dict(),
                    edge.input_schema,
                    edge,
                    stage="input",
                )

    @staticmethod
    def _validate_schema(
        payload: Mapping[str, Any],
        schema: Mapping[str, Any],
        edge: GraphEdge,
        *,
        stage: str,
    ) -> None:
        try:
            validator = JSONSchemaValidator(
                input_schema=schema,
                input_path="context.payload",
                name=f"graph_edge_{stage}",
            )
            validator.check_input(
                AgentRequest(task="validate graph edge", context={"payload": payload})
            )
        except (GuardrailConfigurationError, GuardrailViolation) as error:
            raise GraphSchemaError(
                f"edge {edge.source}->{edge.target} {stage} schema rejected payload: {error}"
            ) from error

    @staticmethod
    def _merge_parallel_updates(
        outcomes: Mapping[str, _NodeOutcome],
        ready_order: list[str],
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        owners: dict[str, str] = {}
        for name in ready_order:
            if name not in outcomes:
                continue
            updates = outcomes[name].updates or {}
            for key, value in updates.items():
                if key in owners:
                    raise GraphMergeConflictError(
                        f"parallel nodes {owners[key]} and {name} both update state key {key}"
                    )
                owners[key] = name
                merged[key] = value
        return merged

    def _propagate_skips(
        self,
        order: tuple[str, ...],
        statuses: dict[str, str],
        edge_decisions: dict[int, bool],
        incoming: Mapping[str, list[int]],
        outgoing: Mapping[str, list[int]],
    ) -> list[str]:
        skipped: list[str] = []
        changed = True
        while changed:
            changed = False
            for name in order:
                if name == self._graph.start or statuses[name] != "pending":
                    continue
                incoming_indexes = incoming[name]
                predecessors_terminal = all(
                    statuses[self._graph.edges[index].source]
                    in {"completed", "skipped"}
                    for index in incoming_indexes
                )
                if predecessors_terminal and not any(
                    edge_decisions.get(index, False)
                    for index in incoming_indexes
                ):
                    statuses[name] = "skipped"
                    skipped.append(name)
                    for edge_index in outgoing[name]:
                        edge_decisions[edge_index] = False
                    changed = True
        return skipped

    def _ready_nodes(
        self,
        order: tuple[str, ...],
        statuses: Mapping[str, str],
        edge_decisions: Mapping[int, bool],
        incoming: Mapping[str, list[int]],
    ) -> list[str]:
        ready: list[str] = []
        for name in order:
            if statuses[name] not in {"pending", "failed"}:
                continue
            if name == self._graph.start:
                ready.append(name)
                continue
            incoming_indexes = incoming[name]
            if not any(edge_decisions.get(index, False) for index in incoming_indexes):
                continue
            if all(
                statuses[self._graph.edges[index].source]
                in {"completed", "skipped"}
                for index in incoming_indexes
            ):
                ready.append(name)
        return ready

    def _edge_indexes(self) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
        incoming = {name: [] for name in self._graph.nodes}
        outgoing = {name: [] for name in self._graph.nodes}
        for index, edge in enumerate(self._graph.edges):
            incoming[edge.target].append(index)
            outgoing[edge.source].append(index)
        return incoming, outgoing

    def _save_checkpoint(
        self,
        signature: str,
        state: GraphState,
        statuses: Mapping[str, str],
        edge_decisions: Mapping[int, bool],
        execution_order: list[str],
        attempts: Mapping[str, int],
        circuit_breakers: Mapping[str, Mapping[str, Any]],
    ) -> None:
        if self._checkpoint_store is None:
            return
        self._checkpoint_store.save(
            GraphCheckpoint(
                graph_signature=signature,
                state=state.to_dict(),
                statuses=dict(statuses),
                edge_decisions={
                    str(index): selected for index, selected in edge_decisions.items()
                },
                execution_order=tuple(execution_order),
                attempts=dict(attempts),
                circuit_breakers=_copy_breakers(circuit_breakers),
            )
        )

    def _load_checkpoint(self, signature: str) -> GraphCheckpoint:
        if self._checkpoint_store is None:
            raise GraphCheckpointError("resume requires a checkpoint store")
        checkpoint = self._checkpoint_store.load()
        if checkpoint is None:
            raise GraphCheckpointError("checkpoint does not exist")
        if checkpoint.graph_signature != signature:
            raise GraphCheckpointError("checkpoint does not match the current graph")
        if set(checkpoint.statuses) != set(self._graph.nodes):
            raise GraphCheckpointError("checkpoint node set does not match the graph")
        return checkpoint

    def _graph_signature(self) -> str:
        policies = {
            name: {
                "max_retries": policy.max_retries,
                "retry_delay_seconds": policy.retry_delay_seconds,
                "timeout_seconds": policy.timeout_seconds,
                "circuit_breaker": (
                    None
                    if policy.circuit_breaker is None
                    else {
                        "failure_threshold": policy.circuit_breaker.failure_threshold,
                        "reset_timeout_seconds": policy.circuit_breaker.reset_timeout_seconds,
                    }
                ),
            }
            for name, policy in self._graph.node_policies.items()
        }
        edges = [
            {
                "source": edge.source,
                "target": edge.target,
                "condition": edge.condition_label or bool(edge.condition),
                "output_schema": edge.output_schema,
                "input_schema": edge.input_schema,
            }
            for edge in self._graph.edges
        ]
        payload = {
            "start": self._graph.start,
            "nodes": list(self._graph.nodes),
            "edges": edges,
            "policies": policies,
            "execution": {
                "strategy": self._graph.execution.strategy,
                "max_workers": self._graph.execution.max_workers,
            },
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _topological_order(self) -> tuple[str, ...]:
        self._validate_structure()
        indegree = {name: 0 for name in self._graph.nodes}
        outgoing = {name: [] for name in self._graph.nodes}
        for edge in self._graph.edges:
            indegree[edge.target] += 1
            outgoing[edge.source].append(edge.target)
        ready = deque(name for name in self._graph.nodes if indegree[name] == 0)
        order: list[str] = []
        while ready:
            current = ready.popleft()
            order.append(current)
            for target in outgoing[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if len(order) != len(self._graph.nodes):
            raise GraphValidationError("graph contains a cycle")
        return tuple(order)

    def _validate_structure(self) -> None:
        if not self._graph.nodes:
            raise GraphValidationError("graph must contain at least one node")
        if self._graph.start not in self._graph.nodes:
            raise GraphValidationError("graph start node does not exist")
        unknown_policies = set(self._graph.node_policies) - set(self._graph.nodes)
        if unknown_policies:
            raise GraphValidationError(
                f"node policies reference unknown nodes: {sorted(unknown_policies)!r}"
            )
        for name, handler in self._graph.nodes.items():
            if not callable(handler):
                raise GraphValidationError(f"node handler is not callable: {name}")
        for edge in self._graph.edges:
            if edge.source not in self._graph.nodes:
                raise GraphValidationError(
                    f"edge source node does not exist: {edge.source}"
                )
            if edge.target not in self._graph.nodes:
                raise GraphValidationError(
                    f"edge target node does not exist: {edge.target}"
                )
            for stage, schema in (
                ("output", edge.output_schema),
                ("input", edge.input_schema),
            ):
                if schema is None:
                    raise GraphValidationError(
                        f"edge {edge.source}->{edge.target} requires {stage}_schema"
                    )
                try:
                    JSONSchemaValidator(
                        input_schema=schema,
                        input_path="context.payload",
                        name=f"graph_edge_{stage}",
                    )
                except GuardrailConfigurationError as error:
                    raise GraphValidationError(
                        f"edge {edge.source}->{edge.target} has invalid {stage} schema: {error}"
                    ) from error

    @staticmethod
    def _raise_execution_error(
        message: str,
        cause: Exception,
        state: GraphState,
        statuses: Mapping[str, str],
        execution_order: list[str],
        attempts: Mapping[str, int],
        circuit_breakers: Mapping[str, Mapping[str, Any]],
        trace: list[GraphEvent],
    ) -> None:
        raise GraphExecutionError(
            message=message,
            state=state,
            statuses=dict(statuses),
            execution_order=tuple(execution_order),
            cause=cause,
            attempts=dict(attempts),
            circuit_breakers=_copy_breakers(circuit_breakers),
            trace=tuple(trace),
        ) from cause
