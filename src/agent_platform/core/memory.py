"""Bounded working memory and versioned local snapshot adapters."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol


class WorkingMemoryContractError(ValueError):
    """A working-memory value violates its stable contract."""


class WorkingMemorySnapshotError(RuntimeError):
    """A memory snapshot is missing, corrupted, or incompatible."""


class MemoryKind(str, Enum):
    PLAN = "plan"
    ACTION = "action"
    OBSERVATION = "observation"
    REFLECTION = "reflection"
    FACT = "fact"


@dataclass(frozen=True)
class MemoryEntry:
    """One ordered, JSON-safe item in current-task working memory."""

    sequence: int
    kind: MemoryKind
    summary: str
    step: int
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise WorkingMemoryContractError(
                "memory entry sequence must be a positive integer"
            )
        try:
            kind = MemoryKind(self.kind)
        except (TypeError, ValueError) as error:
            raise WorkingMemoryContractError(
                "memory entry kind is unsupported"
            ) from error
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise WorkingMemoryContractError(
                "memory entry summary must be a non-empty string"
            )
        if isinstance(self.step, bool) or not isinstance(self.step, int) or self.step < 0:
            raise WorkingMemoryContractError(
                "memory entry step must be a non-negative integer"
            )
        if not isinstance(self.data, Mapping):
            raise WorkingMemoryContractError("memory entry data must be an object")
        normalized_data = _normalize_json_object(self.data)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "summary", self.summary.strip())
        object.__setattr__(self, "data", MappingProxyType(normalized_data))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind.value,
            "summary": self.summary,
            "step": self.step,
            "data": _json_copy(dict(self.data)),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemoryEntry":
        expected = {"sequence", "kind", "summary", "step", "data"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise WorkingMemoryContractError(
                "memory entry fields do not match version 1 contract"
            )
        return cls(
            sequence=payload["sequence"],
            kind=payload["kind"],
            summary=payload["summary"],
            step=payload["step"],
            data=payload["data"],
        )


@dataclass(frozen=True)
class WorkingMemoryView:
    """Immutable memory view exposed to a cognitive agent."""

    capacity: int = 0
    entries: tuple[MemoryEntry, ...] = ()
    dropped_count: int = 0


@dataclass(frozen=True)
class WorkingMemorySnapshot:
    """Versioned data required to restore working memory exactly."""

    version: int
    capacity: int
    next_sequence: int
    dropped_count: int
    entries: tuple[MemoryEntry, ...]

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version != 1:
            raise WorkingMemorySnapshotError(
                f"unsupported working memory snapshot version: {self.version}"
            )
        _validate_positive_int(self.capacity, "snapshot capacity")
        _validate_positive_int(self.next_sequence, "snapshot next_sequence")
        _validate_non_negative_int(self.dropped_count, "snapshot dropped_count")
        entries = tuple(self.entries)
        if any(not isinstance(entry, MemoryEntry) for entry in entries):
            raise WorkingMemorySnapshotError(
                "snapshot entries must contain MemoryEntry values"
            )
        if len(entries) > self.capacity:
            raise WorkingMemorySnapshotError(
                "snapshot contains more entries than its capacity"
            )
        sequences = tuple(entry.sequence for entry in entries)
        if sequences != tuple(sorted(sequences)) or len(sequences) != len(set(sequences)):
            raise WorkingMemorySnapshotError(
                "snapshot entry sequences must be unique and increasing"
            )
        if sequences and self.next_sequence <= sequences[-1]:
            raise WorkingMemorySnapshotError(
                "snapshot next_sequence must exceed all entry sequences"
            )
        object.__setattr__(self, "entries", entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "capacity": self.capacity,
            "next_sequence": self.next_sequence,
            "dropped_count": self.dropped_count,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkingMemorySnapshot":
        expected = {
            "version",
            "capacity",
            "next_sequence",
            "dropped_count",
            "entries",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise WorkingMemorySnapshotError(
                "working memory snapshot fields do not match version 1 contract"
            )
        entries_raw = payload["entries"]
        if not isinstance(entries_raw, list):
            raise WorkingMemorySnapshotError("snapshot entries must be an array")
        try:
            entries = tuple(MemoryEntry.from_dict(item) for item in entries_raw)
            return cls(
                version=payload["version"],
                capacity=payload["capacity"],
                next_sequence=payload["next_sequence"],
                dropped_count=payload["dropped_count"],
                entries=entries,
            )
        except WorkingMemorySnapshotError:
            raise
        except (TypeError, ValueError, WorkingMemoryContractError) as error:
            raise WorkingMemorySnapshotError(
                f"working memory snapshot is invalid: {error}"
            ) from error


class WorkingMemoryStore(Protocol):
    """Snapshot persistence seam shared by memory and JSON adapters."""

    def save(self, snapshot: WorkingMemorySnapshot) -> None:
        """Persist one complete snapshot."""

    def load(self) -> WorkingMemorySnapshot | None:
        """Return the latest snapshot, or None if none exists."""


class WorkingMemory:
    """Bounded FIFO memory for exactly one cognitive task."""

    def __init__(
        self,
        capacity: int = 20,
        *,
        entries: tuple[MemoryEntry, ...] = (),
        next_sequence: int = 1,
        dropped_count: int = 0,
    ) -> None:
        _validate_positive_int(capacity, "working memory capacity")
        _validate_positive_int(next_sequence, "working memory next_sequence")
        _validate_non_negative_int(dropped_count, "working memory dropped_count")
        entries = tuple(entries)
        if any(not isinstance(entry, MemoryEntry) for entry in entries):
            raise WorkingMemoryContractError(
                "working memory entries must contain MemoryEntry values"
            )
        if len(entries) > capacity:
            raise WorkingMemoryContractError(
                "working memory entries exceed capacity"
            )
        sequences = tuple(entry.sequence for entry in entries)
        if sequences != tuple(sorted(sequences)) or len(sequences) != len(set(sequences)):
            raise WorkingMemoryContractError(
                "working memory entry sequences must be unique and increasing"
            )
        if sequences and next_sequence <= sequences[-1]:
            raise WorkingMemoryContractError(
                "working memory next_sequence must exceed all entry sequences"
            )
        self._capacity = capacity
        self._entries = list(entries)
        self._next_sequence = next_sequence
        self._dropped_count = dropped_count

    @property
    def capacity(self) -> int:
        return self._capacity

    def append(
        self,
        kind: MemoryKind,
        summary: str,
        *,
        step: int,
        data: Mapping[str, Any] | None = None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            sequence=self._next_sequence,
            kind=kind,
            summary=summary,
            step=step,
            data=data or {},
        )
        self._next_sequence += 1
        self._entries.append(entry)
        while len(self._entries) > self._capacity:
            self._entries.pop(0)
            self._dropped_count += 1
        return entry

    def view(self) -> WorkingMemoryView:
        return WorkingMemoryView(
            capacity=self._capacity,
            entries=tuple(self._entries),
            dropped_count=self._dropped_count,
        )

    def snapshot(self) -> WorkingMemorySnapshot:
        return WorkingMemorySnapshot(
            version=1,
            capacity=self._capacity,
            next_sequence=self._next_sequence,
            dropped_count=self._dropped_count,
            entries=tuple(self._entries),
        )

    @classmethod
    def restore(
        cls,
        snapshot: WorkingMemorySnapshot | None,
    ) -> "WorkingMemory":
        if snapshot is None:
            raise WorkingMemorySnapshotError("working memory snapshot does not exist")
        if not isinstance(snapshot, WorkingMemorySnapshot):
            raise WorkingMemorySnapshotError(
                "restore requires a WorkingMemorySnapshot"
            )
        return cls(
            capacity=snapshot.capacity,
            entries=snapshot.entries,
            next_sequence=snapshot.next_sequence,
            dropped_count=snapshot.dropped_count,
        )


class InMemoryWorkingMemoryStore:
    """Local adapter used by tests and ephemeral processes."""

    def __init__(self) -> None:
        self._snapshot: WorkingMemorySnapshot | None = None
        self.save_count = 0

    def save(self, snapshot: WorkingMemorySnapshot) -> None:
        if not isinstance(snapshot, WorkingMemorySnapshot):
            raise WorkingMemorySnapshotError(
                "memory store requires a WorkingMemorySnapshot"
            )
        self._snapshot = WorkingMemorySnapshot.from_dict(snapshot.to_dict())
        self.save_count += 1

    def load(self) -> WorkingMemorySnapshot | None:
        if self._snapshot is None:
            return None
        return WorkingMemorySnapshot.from_dict(self._snapshot.to_dict())


class JsonWorkingMemoryStore:
    """Atomically persist one working-memory snapshot as UTF-8 JSON."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, snapshot: WorkingMemorySnapshot) -> None:
        if not isinstance(snapshot, WorkingMemorySnapshot):
            raise WorkingMemorySnapshotError(
                "memory store requires a WorkingMemorySnapshot"
            )
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        except (OSError, TypeError, ValueError) as error:
            raise WorkingMemorySnapshotError(
                f"failed to save working memory snapshot: {error}"
            ) from error

    def load(self) -> WorkingMemorySnapshot | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return WorkingMemorySnapshot.from_dict(payload)
        except WorkingMemorySnapshotError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise WorkingMemorySnapshotError(
                f"failed to load working memory snapshot: {error}"
            ) from error


def _normalize_json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    if any(not isinstance(key, str) for key in value):
        raise WorkingMemoryContractError("memory data keys must be strings")
    try:
        return _json_copy(dict(value))
    except (TypeError, ValueError) as error:
        raise WorkingMemoryContractError(
            f"memory data must be JSON-compatible: {error}"
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


def _validate_positive_int(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _validate_non_negative_int(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
