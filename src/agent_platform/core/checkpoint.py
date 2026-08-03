"""Graph Checkpoint 的 JSON 持久化实现。"""

import json
from dataclasses import dataclass
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


class GraphCheckpointError(RuntimeError):
    """Checkpoint 缺失、损坏或与当前 Graph 不兼容。"""


class JsonCheckpointStore:
    """把单个 Graph Checkpoint 原子写入 JSON 文件。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def save(self, checkpoint: GraphCheckpoint) -> None:
        payload = {
            "graph_signature": checkpoint.graph_signature,
            "state": checkpoint.state,
            "statuses": checkpoint.statuses,
            "edge_decisions": checkpoint.edge_decisions,
            "execution_order": list(checkpoint.execution_order),
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
            return GraphCheckpoint(
                graph_signature=payload["graph_signature"],
                state=dict(payload["state"]),
                statuses=dict(payload["statuses"]),
                edge_decisions={
                    str(key): bool(value)
                    for key, value in payload["edge_decisions"].items()
                },
                execution_order=tuple(payload["execution_order"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise GraphCheckpointError(f"failed to load checkpoint: {error}") from error
