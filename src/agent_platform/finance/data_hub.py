"""Deep financial-data module shared by Agents, scripts, and MCP tools."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


SUPPORTED_FINANCIAL_DATASETS = (
    "market.daily",
    "market.weekly",
    "market.minute",
    "market.realtime",
    "market.fund_flow",
    "fundamental.balance_sheet",
    "fundamental.income_statement",
    "fundamental.cash_flow",
    "fundamental.indicators",
    "fundamental.valuation",
    "macro.index",
    "industry.snapshot",
    "macro.gdp",
    "macro.shibor",
    "macro.policy_lpr",
    "sentiment.news",
    "sentiment.announcements",
    "sentiment.research",
    "tushare.daily",
)

_DATASET_SOURCES = {
    "market.daily": "akshare.stock_zh_a_hist_tx",
    "market.weekly": "akshare.stock_zh_a_hist_tx",
    "market.minute": "akshare.stock_zh_a_minute",
    "market.realtime": "tencent.qt.gtimg.cn",
    "market.fund_flow": "akshare.stock_fund_flow_individual",
    "fundamental.balance_sheet": "akshare.stock_financial_report_sina",
    "fundamental.income_statement": "akshare.stock_financial_report_sina",
    "fundamental.cash_flow": "akshare.stock_financial_report_sina",
    "fundamental.indicators": "akshare.stock_financial_analysis_indicator",
    "fundamental.valuation": "tencent.quote+sina.financial_report",
    "macro.index": "akshare.stock_zh_index_daily",
    "industry.snapshot": "akshare.stock_sector_spot",
    "macro.gdp": "akshare.macro_china_gdp_yearly",
    "macro.shibor": "akshare.macro_china_shibor_all",
    "macro.policy_lpr": "akshare.macro_china_lpr",
    "sentiment.news": "akshare.stock_news_main_cx",
    "sentiment.announcements": "akshare.stock_zh_a_disclosure_report_cninfo",
    "sentiment.research": "akshare.stock_research_report_em",
    "tushare.daily": "tushare.pro.daily",
}

_DEFAULT_TTLS = {
    "market.realtime": 15,
    "market.minute": 60,
    "market.fund_flow": 120,
    "industry.snapshot": 300,
    "sentiment.news": 300,
    "sentiment.announcements": 3600,
    "sentiment.research": 3600,
    "market.weekly": 21600,
    "market.daily": 21600,
    "macro.index": 21600,
    "tushare.daily": 21600,
    "fundamental.valuation": 900,
    "fundamental.balance_sheet": 86400,
    "fundamental.income_statement": 86400,
    "fundamental.cash_flow": 86400,
    "fundamental.indicators": 86400,
    "macro.gdp": 86400,
    "macro.shibor": 3600,
    "macro.policy_lpr": 3600,
}


class FinancialDataErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    UNKNOWN_DATASET = "unknown_dataset"
    AUTH_REQUIRED = "auth_required"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    EMPTY_RESPONSE = "empty_response"
    SCHEMA_MISMATCH = "schema_mismatch"
    CACHE_ERROR = "cache_error"
    FIXTURE_ERROR = "fixture_error"


class FinancialDataError(RuntimeError):
    """Stable error exposed instead of provider-specific exceptions."""

    def __init__(
        self,
        message: str,
        *,
        code: FinancialDataErrorCode,
        source: str = "",
        attempts: int = 0,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.source = source
        self.attempts = attempts
        self.cause = cause


def _aware_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise FinancialDataError(
                f"{field_name} must be an ISO 8601 datetime",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
                cause=error,
            ) from error
    else:
        raise FinancialDataError(
            f"{field_name} must be an ISO 8601 datetime",
            code=FinancialDataErrorCode.SCHEMA_MISMATCH,
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FinancialDataError(
            f"{field_name} must include a timezone",
            code=FinancialDataErrorCode.SCHEMA_MISMATCH,
        )
    return parsed


def _json_value(value: Any, field_name: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item, f"{field_name}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item, field_name) for item in value]
    raise FinancialDataError(
        f"{field_name} contains a non-JSON value: {type(value).__name__}",
        code=FinancialDataErrorCode.SCHEMA_MISMATCH,
    )


@dataclass(frozen=True)
class FinancialDataRecord:
    subject: str
    fields: Mapping[str, Any]
    source: str
    timestamp: datetime
    as_of: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str) or not self.subject.strip():
            raise FinancialDataError(
                "record subject must be a non-empty string",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            )
        if not isinstance(self.source, str) or not self.source.strip():
            raise FinancialDataError(
                "record source must be a non-empty string",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            )
        if not isinstance(self.fields, Mapping):
            raise FinancialDataError(
                "record fields must be an object",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            )
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise FinancialDataError(
                "record timestamp must include a timezone",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            )
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise FinancialDataError(
                "record as_of must include a timezone",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            )
        if self.as_of > self.timestamp:
            raise FinancialDataError(
                "record as_of must not be later than timestamp",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            )
        object.__setattr__(self, "subject", self.subject.strip())
        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(
            self,
            "fields",
            _json_value(dict(self.fields), "fields"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FinancialDataRecord":
        try:
            return cls(
                subject=value["subject"],
                fields=value["fields"],
                source=value["source"],
                timestamp=_aware_datetime(value["timestamp"], "timestamp"),
                as_of=_aware_datetime(value["as_of"], "as_of"),
            )
        except KeyError as error:
            raise FinancialDataError(
                f"record missing required field: {error.args[0]}",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            ) from error

    def to_mapping(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "fields": dict(self.fields),
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "as_of": self.as_of.isoformat(),
        }


@dataclass(frozen=True)
class FinancialDataTraceEvent:
    event: str
    attempt: int = 0
    detail: str = ""


@dataclass(frozen=True)
class FinancialDatasetResult:
    dataset: str
    records: tuple[FinancialDataRecord, ...]
    source: str
    timestamp: datetime
    attempts: int
    cache_hit: bool = False
    mode: str = "live"
    trace: tuple[FinancialDataTraceEvent, ...] = ()

    def __post_init__(self) -> None:
        if self.dataset not in SUPPORTED_FINANCIAL_DATASETS:
            raise FinancialDataError(
                f"unsupported financial dataset: {self.dataset}",
                code=FinancialDataErrorCode.UNKNOWN_DATASET,
            )
        if not self.records:
            raise FinancialDataError(
                "financial dataset must contain at least one record",
                code=FinancialDataErrorCode.EMPTY_RESPONSE,
                source=self.source,
                attempts=self.attempts,
            )
        if not self.source.strip():
            raise FinancialDataError(
                "dataset source must be non-empty",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            )
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise FinancialDataError(
                "dataset timestamp must include a timezone",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            )
        if self.mode not in {"live", "offline"}:
            raise FinancialDataError(
                "dataset mode must be live or offline",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        attempts: int,
        mode: str,
        cache_hit: bool = False,
        trace: Iterable[FinancialDataTraceEvent] = (),
    ) -> "FinancialDatasetResult":
        try:
            records = tuple(
                FinancialDataRecord.from_mapping(record)
                for record in value["records"]
            )
            return cls(
                dataset=value["dataset"],
                records=records,
                source=value["source"],
                timestamp=_aware_datetime(value["timestamp"], "timestamp"),
                attempts=attempts,
                cache_hit=cache_hit,
                mode=mode,
                trace=tuple(trace),
            )
        except KeyError as error:
            raise FinancialDataError(
                f"dataset missing required field: {error.args[0]}",
                code=FinancialDataErrorCode.SCHEMA_MISMATCH,
            ) from error

    def to_mapping(self, *, include_trace: bool = True) -> dict[str, Any]:
        output = {
            "dataset": self.dataset,
            "record_count": len(self.records),
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "attempts": self.attempts,
            "cache_hit": self.cache_hit,
            "mode": self.mode,
            "records": [record.to_mapping() for record in self.records],
        }
        if include_trace:
            output["trace"] = [
                {
                    "event": event.event,
                    "attempt": event.attempt,
                    "detail": event.detail,
                }
                for event in self.trace
            ]
        return output


@dataclass(frozen=True)
class FinancialDataPolicy:
    timeout_seconds: float = 30.0
    max_attempts: int = 2
    backoff_seconds: float = 0.25
    rate_limit_calls: int = 5
    rate_limit_window_seconds: float = 60.0

    def __post_init__(self) -> None:
        if isinstance(self.timeout_seconds, bool) or not 0 < self.timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be greater than 0 and at most 120")
        if isinstance(self.max_attempts, bool) or not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be from 1 to 3")
        if isinstance(self.backoff_seconds, bool) or not 0 <= self.backoff_seconds <= 5:
            raise ValueError("backoff_seconds must be from 0 to 5")
        if isinstance(self.rate_limit_calls, bool) or self.rate_limit_calls < 1:
            raise ValueError("rate_limit_calls must be at least 1")
        if self.rate_limit_window_seconds <= 0:
            raise ValueError("rate_limit_window_seconds must be positive")


class FinancialDataProvider(Protocol):
    def source_for(self, dataset: str) -> str:
        """Return the provider identity before executing the request."""

    def fetch(
        self,
        dataset: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        """Return a normalized provider payload."""


class SlidingWindowRateLimiter:
    """In-process provider limit that rejects instead of waiting indefinitely."""

    def __init__(
        self,
        max_calls: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_calls = max_calls
        self._window_seconds = window_seconds
        self._clock = clock
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, source: str) -> None:
        now = self._clock()
        calls = self._calls[source]
        while calls and now - calls[0] >= self._window_seconds:
            calls.popleft()
        if len(calls) >= self._max_calls:
            raise FinancialDataError(
                f"provider rate limit exceeded for {source}",
                code=FinancialDataErrorCode.RATE_LIMITED,
                source=source,
            )
        calls.append(now)


class JsonFinancialDataCache:
    """Small versioned JSON cache with atomic replacement."""

    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get(
        self,
        key: str,
        *,
        now: datetime,
    ) -> Mapping[str, Any] | None:
        payload = self._read()
        entry = payload["entries"].get(key)
        if entry is None:
            return None
        expires_at = _aware_datetime(entry["expires_at"], "expires_at")
        if expires_at <= now:
            return None
        return entry["value"]

    def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        expires_at: datetime,
    ) -> None:
        payload = self._read()
        payload["entries"][key] = {
            "expires_at": expires_at.isoformat(),
            "value": value,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": self.VERSION, "entries": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FinancialDataError(
                "financial data cache is unreadable",
                code=FinancialDataErrorCode.CACHE_ERROR,
                cause=error,
            ) from error
        if payload.get("version") != self.VERSION or not isinstance(
            payload.get("entries"), dict
        ):
            raise FinancialDataError(
                "financial data cache has an incompatible format",
                code=FinancialDataErrorCode.CACHE_ERROR,
            )
        return payload


class SubprocessFinancialDataProvider:
    """Run true external providers in a killable child process."""

    def __init__(self, worker_path: str | Path | None = None) -> None:
        self._worker_path = Path(worker_path or Path(__file__).with_name("_provider_worker.py"))

    def source_for(self, dataset: str) -> str:
        try:
            return _DATASET_SOURCES[dataset]
        except KeyError as error:
            raise FinancialDataError(
                f"unsupported financial dataset: {dataset}",
                code=FinancialDataErrorCode.UNKNOWN_DATASET,
            ) from error

    def fetch(
        self,
        dataset: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        request = json.dumps(
            {"dataset": dataset, "params": dict(params)},
            ensure_ascii=False,
        )
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        try:
            completed = subprocess.run(
                [sys.executable, str(self._worker_path)],
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise FinancialDataError(
                f"financial provider exceeded the hard timeout for {dataset}",
                code=FinancialDataErrorCode.TIMEOUT,
                source=self.source_for(dataset),
                cause=error,
            ) from error
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise FinancialDataError(
                f"financial provider returned invalid JSON for {dataset}",
                code=FinancialDataErrorCode.PROVIDER_UNAVAILABLE,
                source=self.source_for(dataset),
                cause=error,
            ) from error
        if completed.returncode != 0 or "error" in payload:
            error_payload = payload.get("error", {})
            message = str(error_payload.get("message", "provider worker failed"))
            error_type = str(error_payload.get("type", ""))
            lowered = f"{message} {error_type}".casefold()
            if "token" in lowered or "api init" in lowered:
                code = FinancialDataErrorCode.AUTH_REQUIRED
            elif any(
                marker in lowered
                for marker in (
                    "没有接口",
                    "访问权限",
                    "无权限",
                    "permission denied",
                    "forbidden",
                )
            ):
                code = FinancialDataErrorCode.PERMISSION_DENIED
            else:
                code = FinancialDataErrorCode.PROVIDER_UNAVAILABLE
            raise FinancialDataError(
                f"financial provider failed for {dataset}: {message}",
                code=code,
                source=self.source_for(dataset),
            )
        return payload


class FixtureFinancialDataProvider:
    """Offline adapter for the same normalized payload interface."""

    def __init__(self, fixture_path: str | Path) -> None:
        self._fixture_path = Path(fixture_path)

    def source_for(self, dataset: str) -> str:
        return f"offline_fixture:{dataset}"

    def fetch(
        self,
        dataset: str,
        params: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del params, timeout_seconds
        try:
            payload = json.loads(self._fixture_path.read_text(encoding="utf-8"))
            entry = next(
                item for item in payload["datasets"] if item["dataset"] == dataset
            )
        except (OSError, json.JSONDecodeError, KeyError, StopIteration) as error:
            raise FinancialDataError(
                f"offline fixture is unavailable for {dataset}",
                code=FinancialDataErrorCode.FIXTURE_ERROR,
                source=self.source_for(dataset),
                cause=error,
            ) from error
        return entry


class FinancialDataHub:
    """One deep interface for reliability, provenance, and provider variation."""

    def __init__(
        self,
        *,
        live_provider: FinancialDataProvider,
        offline_provider: FinancialDataProvider,
        cache: JsonFinancialDataCache | None = None,
        policy: FinancialDataPolicy | None = None,
        limiter: SlidingWindowRateLimiter | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._live_provider = live_provider
        self._offline_provider = offline_provider
        self._cache = cache
        self._policy = policy or FinancialDataPolicy()
        self._limiter = limiter or SlidingWindowRateLimiter(
            self._policy.rate_limit_calls,
            self._policy.rate_limit_window_seconds,
        )
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._sleeper = sleeper

    def fetch(
        self,
        dataset: str,
        params: Mapping[str, Any] | None = None,
        *,
        mode: str = "offline",
    ) -> FinancialDatasetResult:
        if dataset not in SUPPORTED_FINANCIAL_DATASETS:
            raise FinancialDataError(
                f"unsupported financial dataset: {dataset}",
                code=FinancialDataErrorCode.UNKNOWN_DATASET,
            )
        if mode not in {"live", "offline"}:
            raise FinancialDataError(
                "mode must be live or offline",
                code=FinancialDataErrorCode.INVALID_REQUEST,
            )
        if params is None:
            normalized_params: Mapping[str, Any] = {}
        elif isinstance(params, Mapping):
            normalized_params = _json_value(dict(params), "params")
        else:
            raise FinancialDataError(
                "params must be an object",
                code=FinancialDataErrorCode.INVALID_REQUEST,
            )
        provider = self._live_provider if mode == "live" else self._offline_provider
        source = provider.source_for(dataset)
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise FinancialDataError(
                "hub clock must return a timezone-aware datetime",
                code=FinancialDataErrorCode.INVALID_REQUEST,
            )
        cache_key = self._cache_key(dataset, normalized_params, source)
        if mode == "live" and self._cache is not None:
            cached = self._cache.get(cache_key, now=now)
            if cached is not None:
                return FinancialDatasetResult.from_mapping(
                    cached,
                    attempts=0,
                    mode="live",
                    cache_hit=True,
                    trace=(FinancialDataTraceEvent("cache.hit"),),
                )

        trace: list[FinancialDataTraceEvent] = [
            FinancialDataTraceEvent("cache.missed" if mode == "live" else "fixture.selected")
        ]
        attempts = 0
        last_error: FinancialDataError | None = None
        for attempt in range(1, self._policy.max_attempts + 1):
            attempts = attempt
            trace.append(FinancialDataTraceEvent("provider.attempt.started", attempt))
            try:
                if mode == "live":
                    self._limiter.acquire(source)
                payload = provider.fetch(
                    dataset,
                    normalized_params,
                    timeout_seconds=self._policy.timeout_seconds,
                )
                result = FinancialDatasetResult.from_mapping(
                    payload,
                    attempts=attempt,
                    mode=mode,
                    trace=(*trace, FinancialDataTraceEvent("provider.response.mapped", attempt)),
                )
            except FinancialDataError as error:
                last_error = error
                trace.append(
                    FinancialDataTraceEvent(
                        "provider.attempt.failed",
                        attempt,
                        error.code.value,
                    )
                )
                if error.code in {
                    FinancialDataErrorCode.AUTH_REQUIRED,
                    FinancialDataErrorCode.PERMISSION_DENIED,
                    FinancialDataErrorCode.RATE_LIMITED,
                    FinancialDataErrorCode.SCHEMA_MISMATCH,
                    FinancialDataErrorCode.FIXTURE_ERROR,
                } or attempt == self._policy.max_attempts:
                    error.attempts = attempt
                    raise
                self._sleeper(self._policy.backoff_seconds * (2 ** (attempt - 1)))
            else:
                if mode == "live" and self._cache is not None:
                    ttl = _DEFAULT_TTLS[dataset]
                    self._cache.put(
                        cache_key,
                        result.to_mapping(include_trace=False),
                        expires_at=now + timedelta(seconds=ttl),
                    )
                return result
        assert last_error is not None
        last_error.attempts = attempts
        raise last_error

    @staticmethod
    def _cache_key(
        dataset: str,
        params: Mapping[str, Any],
        source: str,
    ) -> str:
        serialized = json.dumps(
            {"dataset": dataset, "params": params, "source": source},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class FinancialDataTool:
    """Controlled Tool facade used by Agent loops and the MCP server."""

    name = "financial_data"

    def __init__(self, hub: FinancialDataHub) -> None:
        self._hub = hub

    def run(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise FinancialDataError(
                "financial_data arguments must be an object",
                code=FinancialDataErrorCode.INVALID_REQUEST,
            )
        dataset = arguments.get("dataset")
        if not isinstance(dataset, str) or not dataset.strip():
            raise FinancialDataError(
                "dataset must be a non-empty string",
                code=FinancialDataErrorCode.INVALID_REQUEST,
            )
        params = arguments.get("params", {})
        mode = arguments.get("mode", "offline")
        return self._hub.fetch(dataset.strip(), params, mode=mode).to_mapping()


def build_default_financial_data_tool(
    *,
    project_root: str | Path | None = None,
    policy: FinancialDataPolicy | None = None,
) -> FinancialDataTool:
    root = Path(project_root) if project_root is not None else Path.cwd()
    fixture_path = root / "tests" / "fixtures" / "financial_data_hub.json"
    cache_path = root / ".runtime" / "finance" / "data_cache.json"
    hub = FinancialDataHub(
        live_provider=SubprocessFinancialDataProvider(),
        offline_provider=FixtureFinancialDataProvider(fixture_path),
        cache=JsonFinancialDataCache(cache_path),
        policy=policy,
    )
    return FinancialDataTool(hub)
