"""Graph Checkpoint 的 JSON 持久化实现。"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GraphCheckpoint:
    """恢复 Graph 所需的最小持久化数据。"""

    graph_signature: str
    state: dict[str, Any]
    statuses: dict[str, str]
    edge_decisions: dict[str, bool]
    execution_order: tuple[str, ...]
    attempts: dict[str, int] = field(default_factory=dict)
    circuit_breakers: dict[str, dict[str, Any]] = field(default_factory=dict)


class GraphCheckpointError(RuntimeError):
    """Checkpoint 缺失、损坏或与当前 Graph 不兼容。"""


class JsonCheckpointStore:
    """把单个 Graph Checkpoint 原子写入 JSON 文件。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def save(self, checkpoint: GraphCheckpoint) -> None:
        payload = {
            "version": 2,
            "graph_signature": checkpoint.graph_signature,
            "state": checkpoint.state,
            "statuses": checkpoint.statuses,
            "edge_decisions": checkpoint.edge_decisions,
            "execution_order": list(checkpoint.execution_order),
            "attempts": checkpoint.attempts,
            "circuit_breakers": checkpoint.circuit_breakers,
        }
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        except (OSError, TypeError, ValueError) as error:
            raise GraphCheckpointError(f"failed to save checkpoint: {error}") from error

    def load(self) -> GraphCheckpoint | None:
        if not self.path.exists():
            return None

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise GraphCheckpointError("graph checkpoint root must be an object")
            version = payload.get("version", 1)
            if version not in {1, 2}:
                raise GraphCheckpointError(
                    f"unsupported graph checkpoint version: {version}"
                )
            graph_signature = payload["graph_signature"]
            state = payload["state"]
            statuses = payload["statuses"]
            edge_decisions = payload["edge_decisions"]
            execution_order = payload["execution_order"]
            attempts = payload.get("attempts", {})
            circuit_breakers = payload.get("circuit_breakers", {})
            if not isinstance(graph_signature, str) or not graph_signature:
                raise GraphCheckpointError(
                    "graph checkpoint signature must be a non-empty string"
                )
            if not isinstance(state, dict):
                raise GraphCheckpointError("graph checkpoint state must be an object")
            if not isinstance(statuses, dict) or any(
                not isinstance(name, str)
                or status not in {"pending", "completed", "skipped", "failed"}
                for name, status in statuses.items()
            ):
                raise GraphCheckpointError("graph checkpoint statuses are invalid")
            if not isinstance(edge_decisions, dict) or any(
                not isinstance(value, bool) for value in edge_decisions.values()
            ):
                raise GraphCheckpointError(
                    "graph checkpoint edge decisions are invalid"
                )
            if not isinstance(execution_order, list) or any(
                not isinstance(name, str) for name in execution_order
            ):
                raise GraphCheckpointError(
                    "graph checkpoint execution order is invalid"
                )
            if not isinstance(attempts, dict) or any(
                not isinstance(name, str)
                or isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for name, value in attempts.items()
            ):
                raise GraphCheckpointError("graph checkpoint attempts are invalid")
            normalized_breakers = self._validate_breakers(circuit_breakers)
            return GraphCheckpoint(
                graph_signature=graph_signature,
                state=dict(state),
                statuses=dict(statuses),
                edge_decisions=dict(edge_decisions),
                execution_order=tuple(execution_order),
                attempts=dict(attempts),
                circuit_breakers=normalized_breakers,
            )
        except GraphCheckpointError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise GraphCheckpointError(f"failed to load checkpoint: {error}") from error

    @staticmethod
    def _validate_breakers(value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict):
            raise GraphCheckpointError(
                "graph checkpoint circuit breakers must be an object"
            )
        normalized: dict[str, dict[str, Any]] = {}
        for name, breaker in value.items():
            if not isinstance(name, str) or not isinstance(breaker, dict):
                raise GraphCheckpointError(
                    "graph checkpoint circuit breaker entry is invalid"
                )
            state = breaker.get("state")
            failures = breaker.get("failures")
            opened_at = breaker.get("opened_at")
            if state not in {"closed", "open", "half_open"}:
                raise GraphCheckpointError(
                    f"graph checkpoint circuit state is invalid: {name}"
                )
            if (
                isinstance(failures, bool)
                or not isinstance(failures, int)
                or failures < 0
            ):
                raise GraphCheckpointError(
                    f"graph checkpoint circuit failures are invalid: {name}"
                )
            if opened_at is not None and (
                isinstance(opened_at, bool)
                or not isinstance(opened_at, (int, float))
            ):
                raise GraphCheckpointError(
                    f"graph checkpoint circuit opened_at is invalid: {name}"
                )
            normalized[name] = {
                "state": state,
                "failures": failures,
                "opened_at": opened_at,
            }
        return normalized
