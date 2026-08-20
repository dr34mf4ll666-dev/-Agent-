"""Data quality and reproducibility evidence for one frozen analysis run.

This module deliberately exposes summaries rather than raw market records.  The
customer report can therefore explain whether a result is comparable without
persisting prompts, credentials, or a second copy of the complete snapshot.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


PROVENANCE_SCHEMA_VERSION = 1
DEFAULT_ANALYSIS_CONFIG_VERSION = "analysis-config-v1"
DEFAULT_MODEL_POLICY_VERSION = "p7-policy-v1"

_HEALTHY_SOURCE_STATUSES = frozenset({"primary", "fixture", "cache_fresh"})
_DEGRADED_SOURCE_STATUSES = frozenset({"backup", "cache_stale"})
_UNAVAILABLE_SOURCE_STATUSES = frozenset({"not_available"})


class AnalysisProvenanceError(ValueError):
    """A quality report or run identity cannot be constructed safely."""


@dataclass(frozen=True)
class AnalysisRunIdentity:
    """Stable version inputs used to identify one reproducible report."""

    snapshot_id: str
    security_master_version: str
    code_version: str
    config_version: str
    model_policy_version: str
    report_version: int

    def to_mapping(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "security_master_version": self.security_master_version,
            "code_version": self.code_version,
            "config_version": self.config_version,
            "model_policy_version": self.model_policy_version,
            "report_version": self.report_version,
        }

    @property
    def fingerprint(self) -> str:
        payload = _canonical_json(self.to_mapping())
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DataQualityReport:
    """Small, user-safe quality projection for one immutable snapshot."""

    snapshot_id: str
    symbol: str
    mode: str
    catalog_version: str
    overall_status: str
    comparison_ready: bool
    comparison_note: str
    acquired_at: str | None
    as_of: str | None
    available_count: int
    dataset_count: int
    required_count: int
    required_available_count: int
    degraded_count: int
    unavailable_count: int
    used_backup: bool
    used_cache: bool
    missing_datasets: tuple[str, ...]
    sources: tuple[str, ...]
    items: tuple[Mapping[str, Any], ...]
    security: Mapping[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "mode": self.mode,
            "catalog_version": self.catalog_version,
            "overall_status": self.overall_status,
            "comparison_ready": self.comparison_ready,
            "comparison_note": self.comparison_note,
            "acquired_at": self.acquired_at,
            "as_of": self.as_of,
            "available_count": self.available_count,
            "dataset_count": self.dataset_count,
            "required_count": self.required_count,
            "required_available_count": self.required_available_count,
            "degraded_count": self.degraded_count,
            "unavailable_count": self.unavailable_count,
            "used_backup": self.used_backup,
            "used_cache": self.used_cache,
            "missing_datasets": list(self.missing_datasets),
            "sources": list(self.sources),
            "items": [dict(item) for item in self.items],
            "security": dict(self.security),
        }


class DataQualityRuntime:
    """Evaluate snapshot completeness and provenance through one stable seam."""

    def __init__(
        self,
        *,
        catalog_version: str = "unknown",
        code_version: str = "unknown",
        config_version: str = DEFAULT_ANALYSIS_CONFIG_VERSION,
        model_policy_version: str = DEFAULT_MODEL_POLICY_VERSION,
        report_version: int = 1,
    ) -> None:
        self.catalog_version = _text(catalog_version, "catalog_version")
        self.code_version = _text(code_version, "code_version")
        self.config_version = _text(config_version, "config_version")
        self.model_policy_version = _text(model_policy_version, "model_policy_version")
        if isinstance(report_version, bool) or not isinstance(report_version, int) or report_version < 1:
            raise AnalysisProvenanceError("report_version 必须是正整数。")
        self.report_version = report_version

    def evaluate(
        self,
        snapshot: Any,
        security_record: Any,
        now: datetime,
    ) -> DataQualityReport:
        """Return a deterministic quality summary without retaining raw records."""

        current = _aware_datetime(now, "now")
        snapshot_mapping = _snapshot_mapping(snapshot)
        if snapshot_mapping is None:
            raise AnalysisProvenanceError("分析快照不能为空。")
        datasets = snapshot_mapping.get("datasets")
        if not isinstance(datasets, list):
            raise AnalysisProvenanceError("分析快照缺少 datasets 数组。")

        security = _security_projection(security_record)
        symbol = str(snapshot_mapping.get("symbol", ""))
        if not symbol:
            raise AnalysisProvenanceError("分析快照缺少 symbol。")
        if security.get("symbol") and security["symbol"] != symbol:
            raise AnalysisProvenanceError("快照标的与证券主数据不一致。")

        quality_items: list[Mapping[str, Any]] = []
        required_count = 0
        required_available_count = 0
        degraded_count = 0
        unavailable_count = 0
        invalid_required = False
        invalid_optional = False
        source_values: list[str] = []
        as_of_values: list[str] = []
        missing_datasets: list[str] = []
        used_backup = False
        used_cache = False
        for raw_item in datasets:
            if not isinstance(raw_item, Mapping):
                invalid_required = True
                quality_items.append(
                    {
                        "dataset": "unknown",
                        "required": True,
                        "source_status": "invalid",
                        "quality_status": "invalid",
                        "available": False,
                        "source": "",
                        "timestamp": None,
                        "as_of": None,
                        "freshness": "unknown",
                        "age_seconds": None,
                        "reason": "数据集记录不是对象。",
                        "user_action": "请重新分析，或等待数据源恢复。",
                    }
                )
                continue
            dataset = str(raw_item.get("dataset", "unknown"))
            required = bool(raw_item.get("required", True))
            status = str(raw_item.get("status", "unknown"))
            used_backup |= status == "backup"
            used_cache |= status in {"cache_fresh", "cache_stale"}
            source = str(raw_item.get("source", ""))
            timestamp = _optional_time(raw_item.get("timestamp"))
            as_of = _optional_time(raw_item.get("as_of"))
            freshness = str(raw_item.get("freshness", "unknown"))
            available = status not in _UNAVAILABLE_SOURCE_STATUSES and status != "unknown"
            invalid_reasons: list[str] = []
            if available and not source:
                invalid_reasons.append("缺少来源标识")
            source_lower = source.strip().lower()
            if status == "primary" and (
                source_lower in {"backup", "cache"}
                or source_lower.startswith(("backup:", "cache:"))
            ):
                invalid_reasons.append("来源标识与主源状态不一致")
            if status == "backup" and source_lower.startswith("primary:"):
                invalid_reasons.append("来源标识与备用源状态不一致")
            if available and timestamp is None:
                invalid_reasons.append("缺少获取时间")
            if available and as_of is None:
                invalid_reasons.append("缺少数据对应时间")
            if timestamp is not None and as_of is not None:
                if as_of > timestamp:
                    invalid_reasons.append("数据对应时间晚于获取时间")
                if as_of > current:
                    invalid_reasons.append("数据对应时间晚于当前分析时间")
            if timestamp is not None and timestamp > current:
                invalid_reasons.append("获取时间晚于当前分析时间")

            if invalid_reasons:
                quality_status = "invalid"
                invalid_required |= required
                invalid_optional |= not required
                reason = "；".join(invalid_reasons)
                action = "请重新获取该数据后再比较报告。"
            elif status in _UNAVAILABLE_SOURCE_STATUSES or status == "unknown":
                quality_status = "unavailable"
                unavailable_count += 1
                missing_datasets.append(dataset)
                invalid_required |= False
                reason = str(raw_item.get("detail", "数据源暂时没有返回结果。"))
                action = "该数据缺失，先检查数据源或稍后重试。"
            elif status in _DEGRADED_SOURCE_STATUSES:
                quality_status = "degraded"
                degraded_count += 1
                reason = _degraded_reason(status, freshness)
                action = "可以查看结果，但比较报告时要同时核对数据状态。"
            elif status in _HEALTHY_SOURCE_STATUSES:
                quality_status = "complete"
                reason = "已取得带来源和时间的数据。"
                action = "可以与同样完整的报告比较。"
            else:
                quality_status = "degraded"
                degraded_count += 1
                reason = f"数据源状态 {status} 未列入稳定状态。"
                action = "建议重新获取并确认来源状态。"

            if required:
                required_count += 1
                if quality_status in {"complete", "degraded"}:
                    required_available_count += 1
                if quality_status == "unavailable":
                    invalid_required = True
            if source:
                source_values.append(source)
            if as_of is not None:
                as_of_values.append(as_of.isoformat())
            age_seconds = None
            if timestamp is not None:
                age_seconds = max(0, int((current - timestamp).total_seconds()))
            quality_items.append(
                {
                    "dataset": dataset,
                    "required": required,
                    "source_status": status,
                    "quality_status": quality_status,
                    "available": available and quality_status != "invalid",
                    "source": source,
                    "timestamp": timestamp.isoformat() if timestamp else None,
                    "as_of": as_of.isoformat() if as_of else None,
                    "freshness": freshness,
                    "age_seconds": age_seconds,
                    "reason": reason,
                    "user_action": action,
                }
            )

        if invalid_required or required_count == 0 or required_available_count < required_count:
            overall_status = "blocked"
            comparison_ready = False
            comparison_note = "关键数据不完整，当前结果不适合直接比较。"
        elif degraded_count or unavailable_count or invalid_optional:
            overall_status = "degraded"
            comparison_ready = False
            comparison_note = "部分数据使用备用来源、缓存或缺失，比较时需要先核对数据状态。"
        else:
            overall_status = "complete"
            comparison_ready = True
            comparison_note = "关键数据完整，且来源和时间字段齐全，可以与同样完整的报告比较。"

        return DataQualityReport(
            snapshot_id=str(snapshot_mapping.get("snapshot_id", "unknown")),
            symbol=symbol,
            mode=str(snapshot_mapping.get("mode", "unknown")),
            catalog_version=self.catalog_version,
            overall_status=overall_status,
            comparison_ready=comparison_ready,
            comparison_note=comparison_note,
            acquired_at=_time_text(snapshot_mapping.get("acquired_at")),
            as_of=max(as_of_values) if as_of_values else _time_text(snapshot_mapping.get("as_of")),
            available_count=sum(bool(item.get("available")) for item in quality_items),
            dataset_count=len(datasets),
            required_count=required_count,
            required_available_count=required_available_count,
            degraded_count=degraded_count,
            unavailable_count=unavailable_count,
            used_backup=used_backup,
            used_cache=used_cache,
            missing_datasets=tuple(missing_datasets),
            sources=tuple(dict.fromkeys(source_values)),
            items=tuple(quality_items),
            security=security,
        )

    def build_identity(self, snapshot: Any) -> AnalysisRunIdentity:
        snapshot_mapping = _snapshot_mapping(snapshot)
        return AnalysisRunIdentity(
            snapshot_id=str((snapshot_mapping or {}).get("snapshot_id", "unknown")),
            security_master_version=self.catalog_version,
            code_version=self.code_version,
            config_version=self.config_version,
            model_policy_version=self.model_policy_version,
            report_version=self.report_version,
        )

    def build_provenance(
        self,
        snapshot: Any,
        security_record: Any,
        now: datetime,
    ) -> dict[str, Any]:
        quality = self.evaluate(snapshot, security_record, now)
        identity = self.build_identity(snapshot)
        return {
            "schema_version": PROVENANCE_SCHEMA_VERSION,
            "quality": quality.to_mapping(),
            "identity": identity.to_mapping(),
            "fingerprint": identity.fingerprint,
        }


def unknown_provenance(
    *,
    symbol: str = "",
    reason: str = "历史报告未保存数据质量与运行指纹。",
    catalog_version: str = "unknown",
    report_version: int = 1,
) -> dict[str, Any]:
    """Compatibility projection used for reports created before this schema."""

    identity = AnalysisRunIdentity(
        snapshot_id="unknown",
        security_master_version=catalog_version or "unknown",
        code_version="unknown",
        config_version="unknown",
        model_policy_version="unknown",
        report_version=report_version,
    )
    quality = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "snapshot_id": "unknown",
        "symbol": symbol,
        "mode": "unknown",
        "catalog_version": catalog_version or "unknown",
        "overall_status": "unknown",
        "comparison_ready": False,
        "comparison_note": reason,
        "acquired_at": None,
        "as_of": None,
        "available_count": 0,
        "dataset_count": 0,
        "required_count": 0,
        "required_available_count": 0,
        "degraded_count": 0,
        "unavailable_count": 0,
        "used_backup": False,
        "used_cache": False,
        "missing_datasets": [],
        "sources": [],
        "items": [],
        "security": {},
    }
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "quality": quality,
        "identity": identity.to_mapping(),
        "fingerprint": None,
    }


def _snapshot_mapping(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    method = getattr(value, "to_mapping", None)
    if callable(method):
        try:
            mapped = method(include_records=False)
        except TypeError:
            mapped = method()
        return mapped if isinstance(mapped, Mapping) else None
    return value if isinstance(value, Mapping) else None


def _security_projection(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        source = value
        return {
            "symbol": str(source.get("symbol", "")),
            "code": str(source.get("code", "")),
            "name": str(source.get("name", "")),
            "industry": str(source.get("industry", "")),
        }
    return {
        "symbol": str(getattr(value, "symbol", "")),
        "code": str(getattr(value, "code", "")),
        "name": str(getattr(value, "name", "")),
        "industry": str(getattr(value, "industry", "")),
    }


def _degraded_reason(status: str, freshness: str) -> str:
    if status == "backup":
        return "主数据源失败，已使用备用来源。"
    if status == "cache_stale" or freshness == "历史缓存降级":
        return "实时来源不可用，已使用历史缓存。"
    return "数据没有来自首选来源。"


def _aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AnalysisProvenanceError(f"{field} 必须是带时区的 datetime。")
    return value


def _optional_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _time_text(value: Any) -> str | None:
    parsed = _optional_time(value)
    return parsed.isoformat() if parsed else (str(value) if value not in (None, "") else None)


def _text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AnalysisProvenanceError(f"{field} 不能为空。")
    return text


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "AnalysisProvenanceError",
    "AnalysisRunIdentity",
    "DataQualityReport",
    "DataQualityRuntime",
    "DEFAULT_ANALYSIS_CONFIG_VERSION",
    "DEFAULT_MODEL_POLICY_VERSION",
    "PROVENANCE_SCHEMA_VERSION",
    "unknown_provenance",
]
