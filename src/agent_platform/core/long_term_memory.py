"""Project and organization memory with strict namespace isolation."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol


class LongTermMemoryContractError(ValueError):
    """A long-term memory value violates its stable contract."""


class LongTermMemorySnapshotError(RuntimeError):
    """A long-term memory snapshot is corrupted or incompatible."""


class MemoryScope(str, Enum):
    PROJECT = "project"
    ORGANIZATION = "organization"


class LongTermMemoryCategory(str, Enum):
    FACT = "fact"
    DECISION = "decision"
    ARTIFACT = "artifact"
    PREFERENCE = "preference"
    CONVENTION = "convention"


@dataclass(frozen=True)
class MemoryNamespace:
    """One isolated project or organization namespace."""

    scope: MemoryScope
    identifier: str

    def __post_init__(self) -> None:
        try:
            scope = MemoryScope(self.scope)
        except (TypeError, ValueError) as error:
            raise LongTermMemoryContractError(
                "memory namespace scope is unsupported"
            ) from error
        identifier = _validated_identifier(self.identifier, "namespace identifier")
        if identifier in {".", ".."} or "/" in identifier or "\\" in identifier:
            raise LongTermMemoryContractError(
                "namespace identifier cannot contain path traversal"
            )
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "identifier", identifier)

    def to_dict(self) -> dict[str, str]:
        return {"scope": self.scope.value, "identifier": self.identifier}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemoryNamespace":
        if not isinstance(payload, Mapping) or set(payload) != {
            "scope",
            "identifier",
        }:
            raise LongTermMemoryContractError(
                "memory namespace fields do not match version 1 contract"
            )
        return cls(scope=payload["scope"], identifier=payload["identifier"])


@dataclass(frozen=True)
class LongTermMemoryEntry:
    """One explicitly persisted fact, decision, artifact, or convention."""

    namespace: MemoryNamespace
    key: str
    category: LongTermMemoryCategory
    content: str
    source: str
    created_at: datetime
    updated_at: datetime
    revision: int = 1
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, MemoryNamespace):
            raise LongTermMemoryContractError(
                "long-term memory namespace must be MemoryNamespace"
            )
        key = _validated_identifier(self.key, "memory key")
        try:
            category = LongTermMemoryCategory(self.category)
        except (TypeError, ValueError) as error:
            raise LongTermMemoryContractError(
                "long-term memory category is unsupported"
            ) from error
        content = _validated_text(self.content, "memory content", maximum=10_000)
        source = _validated_text(self.source, "memory source", maximum=256)
        created_at = _validated_datetime(self.created_at, "memory created_at")
        updated_at = _validated_datetime(self.updated_at, "memory updated_at")
        if updated_at < created_at:
            raise LongTermMemoryContractError(
                "memory updated_at cannot be earlier than created_at"
            )
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise LongTermMemoryContractError(
                "memory revision must be a positive integer"
            )
        if not isinstance(self.data, Mapping):
            raise LongTermMemoryContractError("memory data must be an object")
        data = _normalize_json_object(self.data)
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "data", MappingProxyType(data))

    @property
    def identity(self) -> tuple[MemoryScope, str, str]:
        return (self.namespace.scope, self.namespace.identifier, self.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace.to_dict(),
            "key": self.key,
            "category": self.category.value,
            "content": self.content,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "revision": self.revision,
            "data": _json_copy(dict(self.data)),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LongTermMemoryEntry":
        expected = {
            "namespace",
            "key",
            "category",
            "content",
            "source",
            "created_at",
            "updated_at",
            "revision",
            "data",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise LongTermMemoryContractError(
                "long-term memory fields do not match version 1 contract"
            )
        try:
            created_at = datetime.fromisoformat(payload["created_at"])
            updated_at = datetime.fromisoformat(payload["updated_at"])
        except (TypeError, ValueError) as error:
            raise LongTermMemoryContractError(
                "long-term memory timestamps must be ISO 8601 strings"
            ) from error
        return cls(
            namespace=MemoryNamespace.from_dict(payload["namespace"]),
            key=payload["key"],
            category=payload["category"],
            content=payload["content"],
            source=payload["source"],
            created_at=created_at,
            updated_at=updated_at,
            revision=payload["revision"],
            data=payload["data"],
        )


@dataclass(frozen=True)
class LongTermMemorySnapshot:
    version: int
    entries: tuple[LongTermMemoryEntry, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version != 1
        ):
            raise LongTermMemorySnapshotError(
                f"unsupported long-term memory snapshot version: {self.version}"
            )
        entries = tuple(self.entries)
        if any(not isinstance(entry, LongTermMemoryEntry) for entry in entries):
            raise LongTermMemorySnapshotError(
                "snapshot entries must contain LongTermMemoryEntry values"
            )
        identities = tuple(entry.identity for entry in entries)
        if len(identities) != len(set(identities)):
            raise LongTermMemorySnapshotError(
                "snapshot contains duplicate memory identities"
            )
        object.__setattr__(self, "entries", entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LongTermMemorySnapshot":
        if not isinstance(payload, Mapping) or set(payload) != {"version", "entries"}:
            raise LongTermMemorySnapshotError(
                "long-term memory snapshot fields do not match version 1 contract"
            )
        entries_raw = payload["entries"]
        if not isinstance(entries_raw, list):
            raise LongTermMemorySnapshotError("snapshot entries must be an array")
        try:
            return cls(
                version=payload["version"],
                entries=tuple(
                    LongTermMemoryEntry.from_dict(item) for item in entries_raw
                ),
            )
        except LongTermMemorySnapshotError:
            raise
        except (TypeError, ValueError) as error:
            raise LongTermMemorySnapshotError(
                f"long-term memory snapshot is invalid: {error}"
            ) from error


class LongTermMemoryStore(Protocol):
    def save(self, snapshot: LongTermMemorySnapshot) -> None:
        """Persist the complete long-term memory snapshot."""

    def load(self) -> LongTermMemorySnapshot | None:
        """Load the latest snapshot, or None when no snapshot exists."""


class LongTermMemory:
    """Explicit long-term memory isolated by project or organization namespace."""

    def __init__(
        self,
        *,
        store: LongTermMemoryStore,
        clock: Callable[[], datetime],
    ) -> None:
        if not callable(clock):
            raise LongTermMemoryContractError("long-term memory clock must be callable")
        if not callable(getattr(store, "save", None)) or not callable(
            getattr(store, "load", None)
        ):
            raise LongTermMemoryContractError(
                "long-term memory store must provide save and load"
            )
        self._store = store
        self._clock = clock
        snapshot = store.load()
        self._entries = {
            entry.identity: entry
            for entry in (() if snapshot is None else snapshot.entries)
        }

    def upsert(
        self,
        namespace: MemoryNamespace,
        *,
        key: str,
        category: LongTermMemoryCategory,
        content: str,
        source: str,
        data: Mapping[str, Any] | None = None,
    ) -> LongTermMemoryEntry:
        if not isinstance(namespace, MemoryNamespace):
            raise LongTermMemoryContractError(
                "upsert namespace must be MemoryNamespace"
            )
        normalized_key = _validated_identifier(key, "memory key")
        identity = (namespace.scope, namespace.identifier, normalized_key)
        previous = self._entries.get(identity)
        now = _validated_datetime(self._clock(), "memory clock result")
        entry = LongTermMemoryEntry(
            namespace=namespace,
            key=normalized_key,
            category=category,
            content=content,
            source=source,
            created_at=now if previous is None else previous.created_at,
            updated_at=now,
            revision=1 if previous is None else previous.revision + 1,
            data=data or {},
        )
        self._entries[identity] = entry
        self._persist()
        return entry

    def query(
        self,
        namespace: MemoryNamespace,
        *,
        category: LongTermMemoryCategory | None = None,
        text: str | None = None,
        keys: Sequence[str] = (),
        limit: int | None = None,
    ) -> tuple[LongTermMemoryEntry, ...]:
        if not isinstance(namespace, MemoryNamespace):
            raise LongTermMemoryContractError(
                "query namespace must be MemoryNamespace"
            )
        normalized_category = None
        if category is not None:
            try:
                normalized_category = LongTermMemoryCategory(category)
            except (TypeError, ValueError) as error:
                raise LongTermMemoryContractError(
                    "query category is unsupported"
                ) from error
        normalized_text = None
        if text is not None:
            normalized_text = _validated_text(
                text,
                "query text",
                maximum=1_000,
            ).casefold()
        normalized_keys = {
            _validated_identifier(key, "query key") for key in tuple(keys)
        }
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise LongTermMemoryContractError(
                "query limit must be a positive integer"
            )
        matches = []
        for entry in self._entries.values():
            if entry.namespace != namespace:
                continue
            if normalized_category is not None and entry.category is not normalized_category:
                continue
            if normalized_keys and entry.key not in normalized_keys:
                continue
            searchable = f"{entry.key}\n{entry.content}".casefold()
            if normalized_text is not None and normalized_text not in searchable:
                continue
            matches.append(entry)
        matches.sort(key=lambda entry: entry.key)
        if limit is not None:
            matches = matches[:limit]
        return tuple(matches)

    def delete(self, namespace: MemoryNamespace, key: str) -> bool:
        if not isinstance(namespace, MemoryNamespace):
            raise LongTermMemoryContractError(
                "delete namespace must be MemoryNamespace"
            )
        normalized_key = _validated_identifier(key, "memory key")
        identity = (namespace.scope, namespace.identifier, normalized_key)
        if identity not in self._entries:
            return False
        del self._entries[identity]
        self._persist()
        return True

    def snapshot(self) -> LongTermMemorySnapshot:
        return LongTermMemorySnapshot(
            version=1,
            entries=tuple(
                sorted(
                    self._entries.values(),
                    key=lambda entry: (
                        entry.namespace.scope.value,
                        entry.namespace.identifier,
                        entry.key,
                    ),
                )
            ),
        )

    def _persist(self) -> None:
        self._store.save(self.snapshot())


class InMemoryLongTermMemoryStore:
    def __init__(self) -> None:
        self._snapshot: LongTermMemorySnapshot | None = None
        self.save_count = 0

    def save(self, snapshot: LongTermMemorySnapshot) -> None:
        if not isinstance(snapshot, LongTermMemorySnapshot):
            raise LongTermMemorySnapshotError(
                "memory store requires a LongTermMemorySnapshot"
            )
        self._snapshot = LongTermMemorySnapshot.from_dict(snapshot.to_dict())
        self.save_count += 1

    def load(self) -> LongTermMemorySnapshot | None:
        if self._snapshot is None:
            return None
        return LongTermMemorySnapshot.from_dict(self._snapshot.to_dict())


class JsonLongTermMemoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, snapshot: LongTermMemorySnapshot) -> None:
        if not isinstance(snapshot, LongTermMemorySnapshot):
            raise LongTermMemorySnapshotError(
                "memory store requires a LongTermMemorySnapshot"
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
            raise LongTermMemorySnapshotError(
                f"failed to save long-term memory snapshot: {error}"
            ) from error

    def load(self) -> LongTermMemorySnapshot | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return LongTermMemorySnapshot.from_dict(payload)
        except LongTermMemorySnapshotError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise LongTermMemorySnapshotError(
                f"failed to load long-term memory snapshot: {error}"
            ) from error


def _validated_identifier(value: Any, name: str) -> str:
    text = _validated_text(value, name, maximum=128)
    if any(ord(character) < 32 for character in text):
        raise LongTermMemoryContractError(f"{name} cannot contain control characters")
    return text


def _validated_text(value: Any, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LongTermMemoryContractError(f"{name} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum:
        raise LongTermMemoryContractError(
            f"{name} must not exceed {maximum} characters"
        )
    return text


def _validated_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LongTermMemoryContractError(
            f"{name} must be a timezone-aware datetime"
        )
    if value.utcoffset() is None:
        raise LongTermMemoryContractError(
            f"{name} must have a valid UTC offset"
        )
    return value


def _normalize_json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    if any(not isinstance(key, str) for key in value):
        raise LongTermMemoryContractError("memory data keys must be strings")
    try:
        return _json_copy(dict(value))
    except (TypeError, ValueError) as error:
        raise LongTermMemoryContractError(
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
