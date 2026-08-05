"""Persistent per-task working-directory isolation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TaskWorkspaceError(ValueError):
    """A task workspace identifier or path is unsafe."""


@dataclass(frozen=True)
class TaskWorkspace:
    task_id: str
    path: Path

    def __post_init__(self) -> None:
        task_id = _validated_task_id(self.task_id)
        path = Path(self.path).resolve()
        if not path.is_absolute():
            raise TaskWorkspaceError("task workspace path must be absolute")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "path", path)

    def resolve(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise TaskWorkspaceError(
                "workspace relative path cannot be absolute or traverse parents"
            )
        if not relative.parts:
            raise TaskWorkspaceError("workspace relative path cannot be empty")
        candidate = (self.path / relative).resolve()
        if not candidate.is_relative_to(self.path):
            raise TaskWorkspaceError("workspace path escapes the task directory")
        return candidate


class TaskWorkspaceManager:
    """Create or reopen one durable directory for each validated task id."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def open(self, task_id: str) -> TaskWorkspace:
        normalized_id = _validated_task_id(task_id)
        path = (self.root / normalized_id).resolve()
        if not path.is_relative_to(self.root):
            raise TaskWorkspaceError("task workspace escapes configured root")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise TaskWorkspaceError(
                f"failed to create task workspace: {error}"
            ) from error
        return TaskWorkspace(task_id=normalized_id, path=path)


def _validated_task_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskWorkspaceError("task_id must be a non-empty string")
    task_id = value.strip()
    if len(task_id) > 128:
        raise TaskWorkspaceError("task_id must not exceed 128 characters")
    if (
        task_id in {".", ".."}
        or "/" in task_id
        or "\\" in task_id
        or any(ord(character) < 32 for character in task_id)
    ):
        raise TaskWorkspaceError("task_id contains unsafe path characters")
    return task_id
