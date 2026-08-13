"""Immutable data snapshots shared by one complete financial analysis."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from .combined_analysis import CombinedAnalysisQuery
from .data_hub import (
    FinancialDataError,
    FinancialDataErrorCode,
    FinancialDataHub,
    FinancialDataPolicy,
    FinancialDataTool,
    FixtureFinancialDataProvider,
    JsonFinancialDataCache,
    SubprocessFinancialDataProvider,
)
from .fundamental_runtime import DATASET_KEYS


class AnalysisSnapshotError(RuntimeError):
    """The requested analysis cannot obtain a trustworthy data snapshot."""


@dataclass(frozen=True)
class SnapshotDatasetRequest:
    dataset: str
    params: Mapping[str, Any]
    required: bool = True

    def key(self) -> str:
        return _request_key(self.dataset, self.params)


@dataclass(frozen=True)
class SnapshotDataset:
    request: SnapshotDatasetRequest
    status: str
    value: Mapping[str, Any] | None
    source: str
    timestamp: str | None
    as_of: str | None
    freshness: str
    detail: str = ""

    def to_mapping(self, *, include_records: bool = True) -> dict[str, Any]:
        output = {
            "dataset": self.request.dataset,
            "params": deepcopy(dict(self.request.params)),
            "required": self.request.required,
            "status": self.status,
            "source": self.source,
            "timestamp": self.timestamp,
            "as_of": self.as_of,
            "freshness": self.freshness,
            "detail": self.detail,
        }
        if include_records and self.value is not None:
            output["value"] = deepcopy(dict(self.value))
        return output


@dataclass(frozen=True)
class AnalysisSnapshot:
    snapshot_id: str
    symbol: str
    mode: str
    acquired_at: str
    datasets: tuple[SnapshotDataset, ...]

    def __post_init__(self) -> None:
        if not self.snapshot_id or not self.symbol:
            raise AnalysisSnapshotError("snapshot identity is incomplete")
        if self.mode not in {"offline", "live"}:
            raise AnalysisSnapshotError("snapshot mode must be offline or live")
        if not self.datasets:
            raise AnalysisSnapshotError("snapshot must contain datasets")
        keys = [item.request.key() for item in self.datasets]
        if len(keys) != len(set(keys)):
            raise AnalysisSnapshotError("snapshot contains duplicate dataset requests")

    @property
    def degraded(self) -> bool:
        return any(item.status in {"backup", "cache_stale", "not_available"} for item in self.datasets)

    @property
    def available_count(self) -> int:
        return sum(item.value is not None for item in self.datasets)

    def tool(self) -> "SnapshotFinancialDataTool":
        return SnapshotFinancialDataTool(self)

    def dataset(self, dataset: str, params: Mapping[str, Any]) -> SnapshotDataset:
        key = _request_key(dataset, params)
        for item in self.datasets:
            if item.request.key() == key:
                return item
        raise AnalysisSnapshotError(f"dataset request is outside snapshot: {dataset}")

    def to_mapping(self, *, include_records: bool = True) -> dict[str, Any]:
        as_of_values = [item.as_of for item in self.datasets if item.as_of]
        return {
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "mode": self.mode,
            "acquired_at": self.acquired_at,
            "as_of": max(as_of_values) if as_of_values else self.acquired_at,
            "degraded": self.degraded,
            "available_count": self.available_count,
            "dataset_count": len(self.datasets),
            "datasets": [
                item.to_mapping(include_records=include_records) for item in self.datasets
            ],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AnalysisSnapshot":
        try:
            datasets = tuple(
                SnapshotDataset(
                    request=SnapshotDatasetRequest(
                        dataset=str(item["dataset"]),
                        params=deepcopy(dict(item["params"])),
                        required=bool(item.get("required", True)),
                    ),
                    status=str(item["status"]),
                    value=(deepcopy(dict(item["value"])) if item.get("value") is not None else None),
                    source=str(item.get("source", "")),
                    timestamp=item.get("timestamp"),
                    as_of=item.get("as_of"),
                    freshness=str(item.get("freshness", "unknown")),
                    detail=str(item.get("detail", "")),
                )
                for item in value["datasets"]
            )
            return cls(
                snapshot_id=str(value["snapshot_id"]),
                symbol=str(value["symbol"]),
                mode=str(value["mode"]),
                acquired_at=str(value["acquired_at"]),
                datasets=datasets,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AnalysisSnapshotError(f"invalid snapshot payload: {error}") from error


class SnapshotSource(Protocol):
    def fetch(self, dataset: str, params: Mapping[str, Any], *, mode: str) -> Mapping[str, Any]:
        """Return one normalized FinancialDataTool payload."""


class FinancialToolSnapshotSource:
    def __init__(self, tool: FinancialDataTool) -> None:
        self._tool = tool

    def fetch(self, dataset: str, params: Mapping[str, Any], *, mode: str) -> Mapping[str, Any]:
        return self._tool.run({"dataset": dataset, "params": params, "mode": mode})


class JsonSnapshotFallbackCache:
    """Last-known-good cache used only after live primary and backup fail."""

    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def get(self, key: str) -> Mapping[str, Any] | None:
        with self._lock:
            value = self._read()["entries"].get(key)
            return deepcopy(value) if value is not None else None

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        with self._lock:
            payload = self._read()
            payload["entries"][key] = deepcopy(dict(value))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": self.VERSION, "entries": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AnalysisSnapshotError("snapshot fallback cache is unreadable") from error
        if payload.get("version") != self.VERSION or not isinstance(payload.get("entries"), dict):
            raise AnalysisSnapshotError("snapshot fallback cache format is incompatible")
        return payload


class SnapshotFinancialDataTool:
    """Read-only FinancialDataTool-compatible adapter over one frozen snapshot."""

    name = "financial_data"

    def __init__(self, snapshot: AnalysisSnapshot) -> None:
        self._snapshot = snapshot

    def run(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        dataset = str(arguments.get("dataset", ""))
        params = arguments.get("params", {})
        if not isinstance(params, Mapping):
            raise FinancialDataError(
                "snapshot params must be an object",
                code=FinancialDataErrorCode.INVALID_REQUEST,
            )
        try:
            item = self._snapshot.dataset(dataset, params)
        except AnalysisSnapshotError as error:
            raise FinancialDataError(
                str(error), code=FinancialDataErrorCode.INVALID_REQUEST
            ) from error
        if item.value is None:
            code_text = item.detail.split(":", 1)[0]
            try:
                code = FinancialDataErrorCode(code_text)
            except ValueError:
                code = FinancialDataErrorCode.PROVIDER_UNAVAILABLE
            raise FinancialDataError(
                item.detail or f"snapshot dataset unavailable: {dataset}",
                code=code,
                source=item.source,
            )
        return deepcopy(dict(item.value))


class AnalysisSnapshotRuntime:
    """Deep P2 interface: acquire every dataset once and freeze its provenance."""

    def __init__(
        self,
        *,
        primary: SnapshotSource,
        backup: SnapshotSource | None = None,
        fallback_cache: JsonSnapshotFallbackCache | None = None,
        now: Any | None = None,
    ) -> None:
        self._primary = primary
        self._backup = backup
        self._fallback_cache = fallback_cache
        self._now = now or (lambda: datetime.now().astimezone())

    def acquire(self, query: CombinedAnalysisQuery) -> AnalysisSnapshot:
        if not isinstance(query, CombinedAnalysisQuery):
            raise AnalysisSnapshotError("query must be a CombinedAnalysisQuery")
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise AnalysisSnapshotError("snapshot clock must include a timezone")
        datasets: list[SnapshotDataset] = []
        for request in _dataset_requests(query):
            datasets.append(self._acquire_one(request, mode=query.mode))
        unavailable_required = [
            item.request.dataset
            for item in datasets
            if item.request.required and item.value is None
        ]
        if unavailable_required:
            raise AnalysisSnapshotError(
                "关键数据全部来源均不可用: " + ", ".join(unavailable_required)
            )
        return AnalysisSnapshot(
            snapshot_id=uuid4().hex,
            symbol=query.symbol,
            mode=query.mode,
            acquired_at=now.isoformat(timespec="seconds"),
            datasets=tuple(datasets),
        )

    def _acquire_one(self, request: SnapshotDatasetRequest, *, mode: str) -> SnapshotDataset:
        failures: list[FinancialDataError] = []
        try:
            value = self._primary.fetch(request.dataset, request.params, mode=mode)
            status = "fixture" if mode == "offline" else ("cache_fresh" if value.get("cache_hit") else "primary")
            self._remember(request, value, mode=mode)
            return _snapshot_dataset(request, status, value)
        except FinancialDataError as error:
            failures.append(error)
        if mode == "live" and self._backup is not None:
            try:
                value = self._backup.fetch(request.dataset, request.params, mode=mode)
                self._remember(request, value, mode=mode)
                return _snapshot_dataset(request, "backup", value)
            except FinancialDataError as error:
                failures.append(error)
        if mode == "live" and self._fallback_cache is not None:
            cached = self._fallback_cache.get(_cache_key(request, mode))
            if cached is not None:
                return _snapshot_dataset(
                    request,
                    "cache_stale",
                    cached,
                    detail="实时来源失败，使用最近一次可追溯缓存",
                )
        last = failures[-1]
        return SnapshotDataset(
            request=request,
            status="not_available",
            value=None,
            source=last.source,
            timestamp=None,
            as_of=None,
            freshness="unavailable",
            detail=f"{last.code.value}:{last}",
        )

    def _remember(self, request: SnapshotDatasetRequest, value: Mapping[str, Any], *, mode: str) -> None:
        if mode == "live" and self._fallback_cache is not None:
            self._fallback_cache.put(_cache_key(request, mode), value)


def _snapshot_dataset(
    request: SnapshotDatasetRequest,
    status: str,
    value: Mapping[str, Any],
    *,
    detail: str = "",
) -> SnapshotDataset:
    records = value.get("records", [])
    as_of_values = [str(item.get("as_of")) for item in records if isinstance(item, Mapping) and item.get("as_of")]
    return SnapshotDataset(
        request=request,
        status=status,
        value=deepcopy(dict(value)),
        source=str(value.get("source", "")),
        timestamp=str(value.get("timestamp")) if value.get("timestamp") else None,
        as_of=max(as_of_values) if as_of_values else None,
        freshness=_freshness_label(status),
        detail=detail,
    )


def _freshness_label(status: str) -> str:
    return {
        "primary": "实时获取",
        "backup": "备用来源",
        "cache_fresh": "新鲜缓存",
        "cache_stale": "历史缓存降级",
        "fixture": "已验证快照",
        "not_available": "暂不可用",
    }.get(status, status)


def _request_key(dataset: str, params: Mapping[str, Any]) -> str:
    return json.dumps(
        {"dataset": dataset, "params": dict(params)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _cache_key(request: SnapshotDatasetRequest, mode: str) -> str:
    return hashlib.sha256(f"{mode}:{request.key()}".encode("utf-8")).hexdigest()


def _dataset_requests(query: CombinedAnalysisQuery) -> tuple[SnapshotDatasetRequest, ...]:
    technical = query.technical
    fundamental = query.fundamental
    industry = query.industry
    macro = query.macro
    requests = [
        SnapshotDatasetRequest(
            "market.daily",
            {
                "symbol": technical.symbol,
                "start_date": technical.start_date,
                "end_date": technical.end_date,
                "limit": technical.limit,
            },
            required=True,
        )
    ]
    for dataset in DATASET_KEYS.values():
        params: dict[str, Any] = {"symbol": fundamental.symbol, "limit": fundamental.limit}
        if dataset == "fundamental.indicators":
            params["start_year"] = fundamental.start_year
        requests.append(
            SnapshotDatasetRequest(
                dataset,
                params,
                required=dataset not in {"fundamental.indicators"},
            )
        )
    requests.extend(
        (
            SnapshotDatasetRequest("industry.snapshot", {"limit": max(industry.limit, 50)}),
            SnapshotDatasetRequest(
                "macro.policy_lpr",
                {"limit": industry.limit, "start_date": industry.start_date, "end_date": industry.end_date},
            ),
            SnapshotDatasetRequest("macro.index", {"symbol": macro.index_symbol, "limit": macro.limit}),
            SnapshotDatasetRequest("market.fund_flow", {"symbol": macro.symbol, "limit": 1}, required=False),
            SnapshotDatasetRequest("macro.gdp", {"limit": macro.limit}),
            SnapshotDatasetRequest("macro.shibor", {"limit": macro.limit}),
            SnapshotDatasetRequest(
                "sentiment.research",
                {
                    "symbol": macro.symbol,
                    "limit": macro.limit,
                    "start_date": macro.start_date,
                    "end_date": macro.end_date,
                },
                required=False,
            ),
        )
    )
    # LPR is shared by Industry and Macro when their request parameters match.
    unique: dict[str, SnapshotDatasetRequest] = {}
    for request in requests:
        unique.setdefault(request.key(), request)
    return tuple(unique.values())


class _RoutingFixtureProvider:
    def __init__(self, paths: Mapping[str, Path]) -> None:
        self._providers = {
            dataset: FixtureFinancialDataProvider(path) for dataset, path in paths.items()
        }

    def source_for(self, dataset: str) -> str:
        return f"offline_fixture:{dataset}"

    def fetch(self, dataset: str, params: Mapping[str, Any], *, timeout_seconds: float) -> Mapping[str, Any]:
        try:
            provider = self._providers[dataset]
        except KeyError as error:
            raise FinancialDataError(
                f"offline fixture is unavailable for {dataset}",
                code=FinancialDataErrorCode.FIXTURE_ERROR,
                source=self.source_for(dataset),
            ) from error
        return provider.fetch(dataset, params, timeout_seconds=timeout_seconds)


class _TushareDailyBackupSource:
    """Actual secondary source for daily bars; other datasets fall through to cache."""

    def __init__(self, provider: SubprocessFinancialDataProvider, policy: FinancialDataPolicy) -> None:
        self._provider = provider
        self._policy = policy

    def fetch(self, dataset: str, params: Mapping[str, Any], *, mode: str) -> Mapping[str, Any]:
        if mode != "live" or dataset != "market.daily" or not os.getenv("TUSHARE_TOKEN", "").strip():
            raise FinancialDataError(
                f"no backup provider configured for {dataset}",
                code=FinancialDataErrorCode.PROVIDER_UNAVAILABLE,
                source="backup:none",
            )
        symbol = str(params["symbol"])
        ts_code = f"{symbol[2:]}.{symbol[:2].upper()}"
        payload = self._provider.fetch(
            "tushare.daily",
            {**dict(params), "ts_code": ts_code},
            timeout_seconds=self._policy.timeout_seconds,
        )
        records = []
        for record in payload["records"]:
            fields = dict(record["fields"])
            fields["volume_shares"] = str(float(fields.get("vol", 0)) * 100)
            records.append(
                {
                    **dict(record),
                    "subject": symbol,
                    "fields": fields,
                }
            )
        return {
            **dict(payload),
            "dataset": "market.daily",
            "records": records,
        }


def build_default_analysis_snapshot_runtime(
    *,
    project_root: str | Path | None = None,
    policy: FinancialDataPolicy | None = None,
) -> AnalysisSnapshotRuntime:
    root = Path(project_root) if project_root is not None else Path.cwd()
    active_policy = policy or FinancialDataPolicy(timeout_seconds=60.0, max_attempts=1)
    fixture_paths = {
        "market.daily": root / "tests" / "fixtures" / "technical_market_daily_30.json",
        **{
            dataset: root / "tests" / "fixtures" / "fundamental_analysis.json"
            for dataset in DATASET_KEYS.values()
        },
        "industry.snapshot": root / "tests" / "fixtures" / "industry_analysis.json",
        "macro.policy_lpr": root / "tests" / "fixtures" / "industry_analysis.json",
        **{
            dataset: root / "tests" / "fixtures" / "macro_analysis.json"
            for dataset in ("macro.index", "market.fund_flow", "macro.gdp", "macro.shibor", "sentiment.research")
        },
    }
    provider = SubprocessFinancialDataProvider()
    primary_hub = FinancialDataHub(
        live_provider=provider,
        offline_provider=_RoutingFixtureProvider(fixture_paths),
        cache=JsonFinancialDataCache(root / ".runtime" / "finance" / "snapshot_source_cache.json"),
        policy=active_policy,
    )
    return AnalysisSnapshotRuntime(
        primary=FinancialToolSnapshotSource(FinancialDataTool(primary_hub)),
        backup=_TushareDailyBackupSource(provider, active_policy),
        fallback_cache=JsonSnapshotFallbackCache(
            root / ".runtime" / "finance" / "snapshot_fallback_cache.json"
        ),
    )


__all__ = [
    "AnalysisSnapshot",
    "AnalysisSnapshotError",
    "AnalysisSnapshotRuntime",
    "FinancialToolSnapshotSource",
    "JsonSnapshotFallbackCache",
    "SnapshotDataset",
    "SnapshotDatasetRequest",
    "SnapshotFinancialDataTool",
    "SnapshotSource",
    "build_default_analysis_snapshot_runtime",
]
