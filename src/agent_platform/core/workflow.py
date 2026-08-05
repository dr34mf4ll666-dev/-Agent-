"""Safe YAML/JSON adapters for declarative graph definitions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .graph import (
    CircuitBreakerPolicy,
    GraphDefinition,
    GraphEdge,
    GraphExecutionPolicy,
    GraphRunner,
    GraphState,
    GraphValidationError,
    NodeExecutionPolicy,
    NodeHandler,
)


class WorkflowConfigurationError(ValueError):
    """A workflow file cannot be converted into a safe GraphDefinition."""


class NodeRegistry:
    """Allowlist that binds declarative handler names to executable functions."""

    def __init__(self, handlers: Mapping[str, NodeHandler] | None = None) -> None:
        self._handlers: dict[str, NodeHandler] = {}
        for name, handler in (handlers or {}).items():
            self.register(name, handler)

    def register(self, name: str, handler: NodeHandler) -> None:
        if not isinstance(name, str) or not name.strip():
            raise WorkflowConfigurationError(
                "node handler name must be a non-empty string"
            )
        name = name.strip()
        if name in self._handlers:
            raise WorkflowConfigurationError(f"duplicate node handler: {name}")
        if not callable(handler):
            raise WorkflowConfigurationError(
                f"node handler must be callable: {name}"
            )
        self._handlers[name] = handler

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def resolve(self, name: str) -> NodeHandler:
        try:
            return self._handlers[name]
        except KeyError as error:
            raise WorkflowConfigurationError(
                f"unknown node handler: {name}; allowed: {list(self.names)!r}"
            ) from error


class GraphWorkflowLoader:
    """Load one versioned YAML or JSON file through a shared mapping parser."""

    def __init__(self, registry: NodeRegistry) -> None:
        self._registry = registry

    def load(self, path: str | Path) -> GraphDefinition:
        workflow_path = Path(path)
        suffix = workflow_path.suffix.lower()
        try:
            text = workflow_path.read_text(encoding="utf-8")
        except OSError as error:
            raise WorkflowConfigurationError(
                f"failed to read workflow: {error}"
            ) from error

        try:
            if suffix == ".json":
                payload = json.loads(text)
            elif suffix in {".yaml", ".yml"}:
                payload = yaml.safe_load(text)
            else:
                raise WorkflowConfigurationError(
                    "workflow file extension must be .json, .yaml, or .yml"
                )
        except (json.JSONDecodeError, yaml.YAMLError) as error:
            raise WorkflowConfigurationError(
                f"failed to parse workflow: {error}"
            ) from error
        return self.from_mapping(payload)

    def from_mapping(self, payload: Mapping[str, Any]) -> GraphDefinition:
        root = _require_mapping(payload, "workflow")
        version = root.get("version")
        if version != 1:
            raise WorkflowConfigurationError(
                f"workflow version must be 1; received: {version!r}"
            )
        start = _require_string(root.get("start"), "workflow.start")
        nodes_config = _require_mapping(root.get("nodes"), "workflow.nodes")
        if not nodes_config:
            raise WorkflowConfigurationError("workflow.nodes must not be empty")

        nodes: dict[str, NodeHandler] = {}
        policies: dict[str, NodeExecutionPolicy] = {}
        for node_name, raw_node in nodes_config.items():
            name = _require_string(node_name, "workflow node name")
            node = _require_mapping(raw_node, f"workflow.nodes.{name}")
            handler_name = _require_string(
                node.get("handler"),
                f"workflow.nodes.{name}.handler",
            )
            nodes[name] = self._registry.resolve(handler_name)
            policies[name] = self._parse_node_policy(name, node)

        edges_raw = root.get("edges", [])
        if not isinstance(edges_raw, list):
            raise WorkflowConfigurationError("workflow.edges must be an array")
        edges = tuple(
            self._parse_edge(index, raw_edge)
            for index, raw_edge in enumerate(edges_raw)
        )
        execution = self._parse_execution(root.get("execution", {}))
        graph = GraphDefinition(
            start=start,
            nodes=nodes,
            edges=edges,
            node_policies=policies,
            execution=execution,
        )
        try:
            GraphRunner(graph).validate()
        except (GraphValidationError, ValueError, TypeError) as error:
            raise WorkflowConfigurationError(
                f"workflow graph is invalid: {error}"
            ) from error
        return graph

    @staticmethod
    def _parse_execution(raw: Any) -> GraphExecutionPolicy:
        execution = _require_mapping(raw, "workflow.execution")
        try:
            return GraphExecutionPolicy(
                strategy=execution.get("strategy", "sequential"),
                max_workers=execution.get("max_workers", 4),
            )
        except (TypeError, ValueError) as error:
            raise WorkflowConfigurationError(
                f"invalid workflow.execution: {error}"
            ) from error

    @staticmethod
    def _parse_node_policy(
        node_name: str,
        node: Mapping[str, Any],
    ) -> NodeExecutionPolicy:
        retry = _require_mapping(
            node.get("retry", {}),
            f"workflow.nodes.{node_name}.retry",
        )
        breaker_raw = node.get("circuit_breaker")
        breaker = None
        if breaker_raw is not None:
            breaker_mapping = _require_mapping(
                breaker_raw,
                f"workflow.nodes.{node_name}.circuit_breaker",
            )
            try:
                breaker = CircuitBreakerPolicy(
                    failure_threshold=breaker_mapping.get(
                        "failure_threshold", 3
                    ),
                    reset_timeout_seconds=breaker_mapping.get(
                        "reset_timeout_seconds", 60.0
                    ),
                )
            except (TypeError, ValueError) as error:
                raise WorkflowConfigurationError(
                    f"invalid circuit breaker for node {node_name}: {error}"
                ) from error
        try:
            return NodeExecutionPolicy(
                max_retries=retry.get("max_retries", 0),
                retry_delay_seconds=retry.get("delay_seconds", 0.0),
                timeout_seconds=node.get("timeout_seconds"),
                circuit_breaker=breaker,
            )
        except (TypeError, ValueError) as error:
            raise WorkflowConfigurationError(
                f"invalid execution policy for node {node_name}: {error}"
            ) from error

    @staticmethod
    def _parse_edge(index: int, raw: Any) -> GraphEdge:
        edge = _require_mapping(raw, f"workflow.edges[{index}]")
        source = _require_string(
            edge.get("source"), f"workflow.edges[{index}].source"
        )
        target = _require_string(
            edge.get("target"), f"workflow.edges[{index}].target"
        )
        if "output_schema" not in edge or "input_schema" not in edge:
            raise WorkflowConfigurationError(
                f"workflow.edges[{index}] requires output_schema and input_schema"
            )
        output_schema = _require_mapping(
            edge["output_schema"],
            f"workflow.edges[{index}].output_schema",
        )
        input_schema = _require_mapping(
            edge["input_schema"],
            f"workflow.edges[{index}].input_schema",
        )
        condition_raw = edge.get("condition")
        condition = None
        condition_label = ""
        if condition_raw is not None:
            condition_spec = _require_mapping(
                condition_raw,
                f"workflow.edges[{index}].condition",
            )
            condition, condition_label = _build_condition(condition_spec)
        return GraphEdge(
            source=source,
            target=target,
            condition=condition,
            output_schema=output_schema,
            input_schema=input_schema,
            condition_label=condition_label,
        )


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkflowConfigurationError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise WorkflowConfigurationError(f"{path} keys must be strings")
    return value


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowConfigurationError(f"{path} must be a non-empty string")
    return value.strip()


def _resolve_path(state: GraphState, path: str) -> tuple[bool, Any]:
    current: Any = state.values
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _build_condition(spec: Mapping[str, Any]):
    path = _require_string(spec.get("path"), "condition.path")
    operator = _require_string(spec.get("operator"), "condition.operator")
    supported = {
        "eq",
        "ne",
        "in",
        "not_in",
        "gt",
        "gte",
        "lt",
        "lte",
        "exists",
        "truthy",
    }
    if operator not in supported:
        raise WorkflowConfigurationError(
            f"condition.operator is unsupported: {operator}"
        )
    expected = spec.get("value")
    if operator in {"in", "not_in"} and not isinstance(expected, (list, tuple)):
        raise WorkflowConfigurationError(
            f"condition {operator} requires an array value"
        )
    if operator not in {"exists", "truthy"} and "value" not in spec:
        raise WorkflowConfigurationError(
            f"condition {operator} requires value"
        )

    def condition(state: GraphState) -> bool:
        exists, actual = _resolve_path(state, path)
        if operator == "exists":
            return exists
        if not exists:
            return False
        if operator == "truthy":
            return bool(actual)
        if operator == "eq":
            return actual == expected
        if operator == "ne":
            return actual != expected
        if operator == "in":
            return actual in expected
        if operator == "not_in":
            return actual not in expected
        if operator == "gt":
            return actual > expected
        if operator == "gte":
            return actual >= expected
        if operator == "lt":
            return actual < expected
        return actual <= expected

    label = f"{path} {operator}"
    if operator not in {"exists", "truthy"}:
        label += f" {expected!r}"
    return condition, label
