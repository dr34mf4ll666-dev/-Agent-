"""第三周的 Graph/DAG 运行模块。"""

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .checkpoint import (
    GraphCheckpoint,
    GraphCheckpointError,
    JsonCheckpointStore,
)


NodeHandler = Callable[["GraphState"], Mapping[str, Any]]
EdgeCondition = Callable[["GraphState"], bool]


@dataclass(frozen=True)
class GraphState:
    """Graph 节点之间传递的浅层只读状态快照。"""

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
class GraphEdge:
    """从 source 指向 target 的有向边。"""

    source: str
    target: str
    condition: EdgeCondition | None = None


@dataclass(frozen=True)
class GraphDefinition:
    """Graph 的静态节点、边和起点定义。"""

    start: str
    nodes: Mapping[str, NodeHandler]
    edges: tuple[GraphEdge, ...] = ()


@dataclass(frozen=True)
class GraphResult:
    """Graph 成功执行后的状态和节点执行记录。"""

    state: GraphState
    statuses: dict[str, str]
    execution_order: tuple[str, ...]


class GraphValidationError(ValueError):
    """Graph 定义不满足 DAG 结构约束。"""


class GraphContractError(TypeError):
    """节点没有返回符合约定的状态更新。"""


class GraphExecutionError(RuntimeError):
    """Graph 执行失败，并保留失败时的状态和原始异常。"""

    def __init__(
        self,
        message: str,
        state: GraphState,
        statuses: dict[str, str],
        execution_order: tuple[str, ...],
        cause: Exception,
    ) -> None:
        super().__init__(message)
        self.state = state
        self.statuses = statuses
        self.execution_order = execution_order
        self.cause = cause


class GraphRunner:
    """按 DAG 依赖顺序执行节点并合并状态。"""

    def __init__(
        self,
        graph: GraphDefinition,
        *,
        checkpoint_store: JsonCheckpointStore | None = None,
    ) -> None:
        self._graph = graph
        self._checkpoint_store = checkpoint_store

    def run(
        self,
        initial_state: Mapping[str, Any] | GraphState | None = None,
        *,
        resume: bool = False,
    ) -> GraphResult:
        topological_order = self._topological_order()
        signature = self._graph_signature()

        if resume:
            checkpoint = self._load_checkpoint(signature)
            state = GraphState(values=checkpoint.state)
            statuses = dict(checkpoint.statuses)
            edge_decisions = {
                int(index): selected
                for index, selected in checkpoint.edge_decisions.items()
            }
            execution_order = list(checkpoint.execution_order)
        else:
            if isinstance(initial_state, GraphState):
                state = initial_state
            else:
                state = GraphState(values=dict(initial_state or {}))
            statuses = {name: "pending" for name in self._graph.nodes}
            execution_order: list[str] = []
            edge_decisions: dict[int, bool] = {}

        incoming: dict[str, list[int]] = {name: [] for name in self._graph.nodes}
        outgoing: dict[str, list[int]] = {name: [] for name in self._graph.nodes}

        for index, edge in enumerate(self._graph.edges):
            incoming[edge.target].append(index)
            outgoing[edge.source].append(index)

        for node_name in topological_order:
            if statuses[node_name] in {"completed", "skipped"}:
                continue

            if node_name != self._graph.start:
                selected = any(
                    edge_decisions.get(index, False) for index in incoming[node_name]
                )
                if not selected:
                    statuses[node_name] = "skipped"
                    for edge_index in outgoing[node_name]:
                        edge_decisions[edge_index] = False
                    self._save_checkpoint(
                        signature,
                        state,
                        statuses,
                        edge_decisions,
                        execution_order,
                    )
                    continue

            try:
                updates = self._graph.nodes[node_name](state)
                if not isinstance(updates, Mapping):
                    raise GraphContractError(
                        f"node {node_name} must return a mapping of state updates"
                    )

                candidate_state = state.with_updates(updates)
                candidate_decisions: dict[int, bool] = {}
                for edge_index in outgoing[node_name]:
                    edge = self._graph.edges[edge_index]
                    candidate_decisions[edge_index] = (
                        True
                        if edge.condition is None
                        else bool(edge.condition(candidate_state))
                    )

                state = candidate_state
                edge_decisions.update(candidate_decisions)
                statuses[node_name] = "completed"
                execution_order.append(node_name)
                self._save_checkpoint(
                    signature,
                    state,
                    statuses,
                    edge_decisions,
                    execution_order,
                )
            except Exception as error:
                statuses[node_name] = "failed"
                self._save_checkpoint(
                    signature,
                    state,
                    statuses,
                    edge_decisions,
                    execution_order,
                )
                raise GraphExecutionError(
                    message=f"graph failed at node {node_name}: {error}",
                    state=state,
                    statuses=dict(statuses),
                    execution_order=tuple(execution_order),
                    cause=error,
                ) from error

        return GraphResult(
            state=state,
            statuses=statuses,
            execution_order=tuple(execution_order),
        )

    def _save_checkpoint(
        self,
        signature: str,
        state: GraphState,
        statuses: dict[str, str],
        edge_decisions: dict[int, bool],
        execution_order: list[str],
    ) -> None:
        if self._checkpoint_store is None:
            return

        self._checkpoint_store.save(
            GraphCheckpoint(
                graph_signature=signature,
                state=state.to_dict(),
                statuses=dict(statuses),
                edge_decisions={
                    str(index): selected
                    for index, selected in edge_decisions.items()
                },
                execution_order=tuple(execution_order),
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
        node_part = ",".join(self._graph.nodes)
        edge_part = ",".join(
            f"{edge.source}->{edge.target}:{edge.condition is not None}"
            for edge in self._graph.edges
        )
        return f"start={self._graph.start}|nodes={node_part}|edges={edge_part}"

    def _topological_order(self) -> tuple[str, ...]:
        self._validate_structure()
        indegree = {name: 0 for name in self._graph.nodes}
        outgoing: dict[str, list[str]] = {name: [] for name in self._graph.nodes}

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

        for edge in self._graph.edges:
            if edge.source not in self._graph.nodes:
                raise GraphValidationError(
                    f"edge source node does not exist: {edge.source}"
                )
            if edge.target not in self._graph.nodes:
                raise GraphValidationError(
                    f"edge target node does not exist: {edge.target}"
                )
