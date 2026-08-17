"""Versioned securities master data for the customer research catalog."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


class SecurityMasterError(ValueError):
    """The securities master file is invalid or a lookup is not possible."""


_SYMBOL_PATTERN = re.compile(r"^(?:sh|sz)\d{6}$")
_VALID_MODES = frozenset({"offline", "live"})


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise SecurityMasterError(f"证券主数据字段 {field} 不能为空。")
    return result


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise SecurityMasterError(f"证券主数据字段 {field} 必须是非空数组。")
    output = tuple(_text(item, field) for item in value)
    if len(set(output)) != len(output):
        raise SecurityMasterError(f"证券主数据字段 {field} 不能有重复项。")
    return output


@dataclass(frozen=True)
class SecurityMasterRecord:
    """One validated record exposed by the securities master."""

    symbol: str
    code: str
    name: str
    exchange: str
    industry: str
    listing_status: str
    verified: bool
    customer_visible: bool
    analysis_sectors: Mapping[str, str]
    available_modes: tuple[str, ...]
    available_sources: tuple[str, ...]
    capabilities: Mapping[str, bool]
    snapshot: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, index: int) -> SecurityMasterRecord:
        if not isinstance(value, Mapping):
            raise SecurityMasterError(f"证券主数据 records[{index}] 必须是对象。")
        symbol = _text(value.get("symbol"), f"records[{index}].symbol").lower()
        if not _SYMBOL_PATTERN.fullmatch(symbol):
            raise SecurityMasterError(f"证券主数据 records[{index}].symbol 格式无效。")
        code = _text(value.get("code"), f"records[{index}].code")
        if code != symbol[2:] or not code.isdigit():
            raise SecurityMasterError(f"证券主数据 records[{index}].code 与 symbol 不一致。")
        analysis_sectors = value.get("analysis_sectors")
        if not isinstance(analysis_sectors, Mapping) or not analysis_sectors:
            raise SecurityMasterError(
                f"证券主数据 records[{index}].analysis_sectors 必须是非空对象。"
            )
        normalized_sectors = {
            str(mode).strip().lower(): _text(sector, f"records[{index}].analysis_sectors")
            for mode, sector in analysis_sectors.items()
        }
        if set(normalized_sectors) - _VALID_MODES:
            raise SecurityMasterError(
                f"证券主数据 records[{index}].analysis_sectors 只能使用 offline/live。"
            )
        available_modes = _string_tuple(value.get("available_modes"), f"records[{index}].available_modes")
        if set(available_modes) != set(normalized_sectors):
            raise SecurityMasterError(
                f"证券主数据 records[{index}] 的 available_modes 与 analysis_sectors 不一致。"
            )
        capabilities = value.get("capabilities")
        if not isinstance(capabilities, Mapping) or not capabilities:
            raise SecurityMasterError(f"证券主数据 records[{index}].capabilities 必须是对象。")
        normalized_capabilities = {str(key): bool(item) for key, item in capabilities.items()}
        snapshot = value.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise SecurityMasterError(f"证券主数据 records[{index}].snapshot 必须是对象。")
        verified = value.get("verified") is True
        customer_visible = value.get("customer_visible") is True
        if customer_visible and not verified:
            raise SecurityMasterError(
                f"证券主数据 records[{index}] 未验证，不能进入客户正式目录。"
            )
        if customer_visible and value.get("listing_status") != "listed":
            raise SecurityMasterError(
                f"证券主数据 records[{index}] 非上市状态，不能进入客户正式目录。"
            )
        return cls(
            symbol=symbol,
            code=code,
            name=_text(value.get("name"), f"records[{index}].name"),
            exchange=_text(value.get("exchange"), f"records[{index}].exchange"),
            industry=_text(value.get("industry"), f"records[{index}].industry"),
            listing_status=_text(value.get("listing_status"), f"records[{index}].listing_status"),
            verified=verified,
            customer_visible=customer_visible,
            analysis_sectors=normalized_sectors,
            available_modes=available_modes,
            available_sources=_string_tuple(value.get("available_sources"), f"records[{index}].available_sources"),
            capabilities=normalized_capabilities,
            snapshot=dict(snapshot),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "code": self.code,
            "name": self.name,
            "exchange": self.exchange,
            "industry": self.industry,
            "listing_status": self.listing_status,
            "verified": self.verified,
            "customer_visible": self.customer_visible,
            "analysis_sectors": dict(self.analysis_sectors),
            "available_modes": list(self.available_modes),
            "available_sources": list(self.available_sources),
            "capabilities": dict(self.capabilities),
            "snapshot": dict(self.snapshot),
        }

    def to_legacy_mapping(self) -> dict[str, Any]:
        """Keep the old internal shape stable while callers migrate to the master."""

        return {
            "name": self.name,
            "exchange": self.exchange,
            "sectors": dict(self.analysis_sectors),
        }


class SecurityMasterRuntime:
    """Deep module hiding JSON loading, validation, filtering, and compatibility."""

    def __init__(self, *, catalog_version: str, records: Iterable[SecurityMasterRecord]) -> None:
        self.catalog_version = _text(catalog_version, "catalog_version")
        materialized = tuple(records)
        if not materialized:
            raise SecurityMasterError("证券主数据至少需要一条记录。")
        symbols = [record.symbol for record in materialized]
        if len(set(symbols)) != len(symbols):
            raise SecurityMasterError("证券主数据 symbol 不能重复。")
        self._records = materialized
        self._by_symbol = {record.symbol: record for record in materialized}

    @classmethod
    def from_json(cls, path: str | Path) -> SecurityMasterRuntime:
        file_path = Path(path)
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SecurityMasterError(f"无法读取证券主数据: {file_path}") from error
        if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
            raise SecurityMasterError("证券主数据 schema_version 必须是 1。")
        raw_records = payload.get("records")
        if not isinstance(raw_records, list):
            raise SecurityMasterError("证券主数据 records 必须是数组。")
        records = tuple(
            SecurityMasterRecord.from_mapping(item, index=index)
            for index, item in enumerate(raw_records)
        )
        return cls(catalog_version=payload.get("catalog_version"), records=records)

    @classmethod
    def from_project(cls, project_root: str | Path | None = None) -> SecurityMasterRuntime:
        if project_root is None:
            path = Path(__file__).with_name("resources") / "security_master.v1.json"
        else:
            path = Path(project_root) / "src" / "agent_platform" / "resources" / "security_master.v1.json"
        return cls.from_json(path)

    def get(self, symbol: str, *, include_unverified: bool = False) -> SecurityMasterRecord:
        normalized = str(symbol).strip().lower()
        try:
            record = self._by_symbol[normalized]
        except KeyError as error:
            raise SecurityMasterError("当前证券不在证券主数据中。") from error
        if not include_unverified and not (record.verified and record.customer_visible):
            raise SecurityMasterError("当前证券尚未通过验证，不能进入客户正式目录。")
        return record

    def search(
        self,
        *,
        query: str = "",
        industry: str | None = None,
        exchange: str | None = None,
        include_unverified: bool = False,
    ) -> tuple[SecurityMasterRecord, ...]:
        query_text = str(query).strip().lower()
        industry_text = str(industry).strip() if industry else ""
        exchange_text = str(exchange).strip() if exchange else ""
        output = []
        for record in self._records:
            if not include_unverified and not (record.verified and record.customer_visible):
                continue
            haystack = f"{record.symbol} {record.code} {record.name}".lower()
            if query_text and query_text not in haystack:
                continue
            if industry_text and record.industry != industry_text:
                continue
            if exchange_text and record.exchange != exchange_text:
                continue
            output.append(record)
        return tuple(output)

    def customer_records(self) -> tuple[SecurityMasterRecord, ...]:
        return self.search()

    def industries(self, *, include_unverified: bool = False) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    record.industry
                    for record in self.search(include_unverified=include_unverified)
                }
            )
        )

    def legacy_mapping(self) -> dict[str, dict[str, Any]]:
        return {
            record.symbol: record.to_legacy_mapping()
            for record in self.customer_records()
        }

    def overview_records(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": record.symbol,
                "code": record.code,
                "name": record.name,
                "exchange": record.exchange,
                "industry": record.industry,
                "listing_status": record.listing_status,
                "verified": record.verified,
                "modes": list(record.available_modes),
                "available_sources": list(record.available_sources),
                "capabilities": dict(record.capabilities),
                "snapshot": dict(record.snapshot),
            }
            for record in self.customer_records()
        ]


DEFAULT_SECURITY_MASTER = SecurityMasterRuntime.from_project()


__all__ = [
    "DEFAULT_SECURITY_MASTER",
    "SecurityMasterError",
    "SecurityMasterRecord",
    "SecurityMasterRuntime",
]
