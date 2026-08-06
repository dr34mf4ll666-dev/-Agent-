"""Stable contracts for the non-financial local research example."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class ResearchContractError(ValueError):
    """A research-domain value does not satisfy its public contract."""


def require_timestamp(value: Any, field_name: str) -> str:
    """Return a timezone-aware ISO-8601 timestamp or raise a stable error."""

    if not isinstance(value, str) or not value.strip():
        raise ResearchContractError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise ResearchContractError(
            f"{field_name} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResearchContractError(f"{field_name} must include a timezone")
    return normalized


def require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchContractError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ResearchDocument:
    """One local document with explicit provenance and knowledge time."""

    document_id: str
    title: str
    content: str
    source: str
    timestamp: str
    as_of: str

    def __post_init__(self) -> None:
        for field_name in ("document_id", "title", "content", "source"):
            object.__setattr__(
                self,
                field_name,
                require_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "timestamp",
            require_timestamp(self.timestamp, "timestamp"),
        )
        object.__setattr__(self, "as_of", require_timestamp(self.as_of, "as_of"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ResearchDocument":
        if not isinstance(value, Mapping):
            raise ResearchContractError("document must be an object")
        try:
            return cls(
                document_id=value["document_id"],
                title=value["title"],
                content=value["content"],
                source=value["source"],
                timestamp=value["timestamp"],
                as_of=value["as_of"],
            )
        except KeyError as error:
            raise ResearchContractError(
                f"document is missing field: {error.args[0]}"
            ) from error

    def to_dict(self) -> dict[str, str]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "content": self.content,
            "source": self.source,
            "timestamp": self.timestamp,
            "as_of": self.as_of,
        }
