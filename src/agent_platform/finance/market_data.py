"""Traceable daily market-data adapters and their controlled Tool surface."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .contracts import MarketDataSeries, MarketDataValidationError


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
TENCENT_SOURCE = "akshare.stock_zh_a_hist_tx"
_SYMBOL_PATTERN = re.compile(r"^(?:sh|sz|bj)\d{6}$")
_REQUIRED_TENCENT_COLUMNS = {
    "date",
    "open",
    "close",
    "high",
    "low",
    "amount",
}


class MarketDataRequestError(ValueError):
    """A caller supplied an unsafe or unsupported daily-bar query."""


class MarketDataErrorCode(str, Enum):
    """Stable error categories exposed above provider-specific exceptions."""

    DEPENDENCY_MISSING = "dependency_missing"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    EMPTY_RESPONSE = "empty_response"
    SCHEMA_MISMATCH = "schema_mismatch"
    FIXTURE_ERROR = "fixture_error"


class MarketDataProviderError(RuntimeError):
    """A normalized provider failure with the original exception as cause."""

    def __init__(
        self,
        message: str,
        *,
        code: MarketDataErrorCode,
        source: str,
        attempts: int,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.source = source
        self.attempts = attempts
        self.cause = cause


def _parse_query_date(value: Any, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise MarketDataRequestError(f"{field_name} must use YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as error:
        raise MarketDataRequestError(f"{field_name} must use YYYYMMDD") from error


@dataclass(frozen=True)
class DailyBarQuery:
    """The deliberately small query supported by the first B1 slice."""

    symbol: str
    start_date: date
    end_date: date
    adjust: str = ""

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().lower() if isinstance(self.symbol, str) else ""
        if not _SYMBOL_PATTERN.fullmatch(symbol):
            raise MarketDataRequestError(
                "symbol must include a market prefix, for example sz000001"
            )
        if not isinstance(self.start_date, date) or isinstance(
            self.start_date, datetime
        ):
            raise MarketDataRequestError("start_date must be a date")
        if not isinstance(self.end_date, date) or isinstance(self.end_date, datetime):
            raise MarketDataRequestError("end_date must be a date")
        if self.start_date > self.end_date:
            raise MarketDataRequestError("start_date must not be later than end_date")
        if (self.end_date - self.start_date).days > 366:
            raise MarketDataRequestError(
                "the first B1 slice limits one query to at most 366 days"
            )
        if self.adjust != "":
            raise MarketDataRequestError(
                "the first B1 slice supports unadjusted daily bars only"
            )
        object.__setattr__(self, "symbol", symbol)

    @classmethod
    def from_arguments(cls, arguments: Mapping[str, Any]) -> "DailyBarQuery":
        if not isinstance(arguments, Mapping):
            raise MarketDataRequestError("daily-bar arguments must be an object")
        try:
            symbol = arguments["symbol"]
            start_date = arguments["start_date"]
            end_date = arguments["end_date"]
        except KeyError as error:
            raise MarketDataRequestError(
                f"missing required argument: {error.args[0]}"
            ) from error
        return cls(
            symbol=symbol,
            start_date=_parse_query_date(start_date, "start_date"),
            end_date=_parse_query_date(end_date, "end_date"),
            adjust=arguments.get("adjust", ""),
        )

    def to_provider_arguments(self, timeout_seconds: float) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start_date": self.start_date.strftime("%Y%m%d"),
            "end_date": self.end_date.strftime("%Y%m%d"),
            "adjust": self.adjust,
            "timeout": timeout_seconds,
        }


@dataclass(frozen=True)
class MarketDataFetchPolicy:
    timeout_seconds: float = 8.0
    max_attempts: int = 2
    backoff_seconds: float = 0.2

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 30
        ):
            raise MarketDataRequestError("timeout_seconds must be from 0 to 30")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= 3
        ):
            raise MarketDataRequestError("max_attempts must be from 1 to 3")
        if (
            isinstance(self.backoff_seconds, bool)
            or not isinstance(self.backoff_seconds, (int, float))
            or not 0 <= self.backoff_seconds <= 5
        ):
            raise MarketDataRequestError("backoff_seconds must be from 0 to 5")


@dataclass(frozen=True)
class MarketDataTraceEvent:
    event: str
    attempt: int
    detail: str = ""


@dataclass(frozen=True)
class MarketDataFetchResult:
    series: MarketDataSeries
    source: str
    fetched_at: datetime
    attempts: int
    trace: tuple[MarketDataTraceEvent, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "symbol": self.series.symbol,
            "bar_count": len(self.series.bars),
            "source": self.source,
            "timestamp": self.fetched_at.isoformat(),
            "bars": [
                {
                    "symbol": bar.symbol,
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": bar.volume,
                    "source": bar.source,
                    "timestamp": bar.timestamp.isoformat(),
                    "as_of": bar.as_of.isoformat(),
                }
                for bar in self.series.bars
            ],
            "attempts": self.attempts,
            "trace": [
                {
                    "event": event.event,
                    "attempt": event.attempt,
                    "detail": event.detail,
                }
                for event in self.trace
            ],
        }


class DailyMarketDataProvider(Protocol):
    source: str

    def fetch(
        self,
        query: DailyBarQuery,
        policy: MarketDataFetchPolicy,
    ) -> MarketDataFetchResult:
        """Fetch one bounded, traceable daily series."""


class AkShareTencentDailyAdapter:
    """Map AKShare's real Tencent response into the stable market contract."""

    source = TENCENT_SOURCE

    def __init__(
        self,
        transport: Callable[..., Any] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(SHANGHAI_TIMEZONE))
        self._sleeper = sleeper

    def fetch(
        self,
        query: DailyBarQuery,
        policy: MarketDataFetchPolicy,
    ) -> MarketDataFetchResult:
        transport = self._transport or self._load_transport()
        trace: list[MarketDataTraceEvent] = []
        raw_result: Any = None
        for attempt in range(1, policy.max_attempts + 1):
            trace.append(MarketDataTraceEvent("provider.attempt.started", attempt))
            try:
                raw_result = transport(
                    **query.to_provider_arguments(policy.timeout_seconds)
                )
            except Exception as error:
                trace.append(
                    MarketDataTraceEvent(
                        "provider.attempt.failed",
                        attempt,
                        type(error).__name__,
                    )
                )
                if attempt == policy.max_attempts:
                    raise MarketDataProviderError(
                        "Tencent daily market-data provider is unavailable",
                        code=MarketDataErrorCode.PROVIDER_UNAVAILABLE,
                        source=self.source,
                        attempts=attempt,
                        cause=error,
                    ) from error
                self._sleeper(policy.backoff_seconds * (2 ** (attempt - 1)))
            else:
                trace.append(
                    MarketDataTraceEvent("provider.attempt.succeeded", attempt)
                )
                break

        fetched_at = self._clock()
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise MarketDataRequestError("adapter clock must return a timezone-aware time")
        records = self._response_records(raw_result)
        if not records:
            raise MarketDataProviderError(
                "Tencent daily market-data provider returned no bars",
                code=MarketDataErrorCode.EMPTY_RESPONSE,
                source=self.source,
                attempts=attempt,
            )
        try:
            series = self._map_records(records, query, fetched_at)
        except (KeyError, TypeError, ValueError, InvalidOperation, MarketDataValidationError) as error:
            raise MarketDataProviderError(
                "Tencent daily market-data response does not match the verified schema",
                code=MarketDataErrorCode.SCHEMA_MISMATCH,
                source=self.source,
                attempts=attempt,
                cause=error,
            ) from error
        trace.append(MarketDataTraceEvent("provider.response.mapped", attempt))
        return MarketDataFetchResult(
            series=series,
            source=self.source,
            fetched_at=fetched_at,
            attempts=attempt,
            trace=tuple(trace),
        )

    def _load_transport(self) -> Callable[..., Any]:
        try:
            import akshare as ak
        except ImportError as error:
            raise MarketDataProviderError(
                "AKShare is required for live Tencent market data",
                code=MarketDataErrorCode.DEPENDENCY_MISSING,
                source=self.source,
                attempts=0,
                cause=error,
            ) from error
        return ak.stock_zh_a_hist_tx

    @staticmethod
    def _response_records(raw_result: Any) -> list[Mapping[str, Any]]:
        if raw_result is None:
            return []
        if hasattr(raw_result, "to_dict"):
            records = raw_result.to_dict(orient="records")
        elif isinstance(raw_result, Iterable) and not isinstance(
            raw_result, (str, bytes, Mapping)
        ):
            records = list(raw_result)
        else:
            raise TypeError("provider response must be DataFrame-like or record iterable")
        if any(not isinstance(record, Mapping) for record in records):
            raise TypeError("provider response rows must be mappings")
        return records

    def _map_records(
        self,
        records: list[Mapping[str, Any]],
        query: DailyBarQuery,
        fetched_at: datetime,
    ) -> MarketDataSeries:
        mapped: list[dict[str, Any]] = []
        for record in records:
            missing = _REQUIRED_TENCENT_COLUMNS.difference(record)
            if missing:
                raise KeyError(", ".join(sorted(missing)))
            trading_date = self._provider_date(record["date"])
            volume_lots = Decimal(str(record["amount"]))
            volume_shares = volume_lots * Decimal("100")
            if not volume_shares.is_finite() or volume_shares != volume_shares.to_integral_value():
                raise ValueError("Tencent amount must map to a whole share volume")
            mapped.append(
                {
                    "symbol": query.symbol,
                    "open": record["open"],
                    "high": record["high"],
                    "low": record["low"],
                    "close": record["close"],
                    "volume": int(volume_shares),
                    "source": self.source,
                    "timestamp": fetched_at,
                    "as_of": datetime(
                        trading_date.year,
                        trading_date.month,
                        trading_date.day,
                        15,
                        tzinfo=SHANGHAI_TIMEZONE,
                    ),
                }
            )
        mapped.sort(key=lambda item: item["as_of"])
        return MarketDataSeries.from_records(mapped)

    @staticmethod
    def _provider_date(value: Any) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return date.fromisoformat(value)
        date_method = getattr(value, "date", None)
        if callable(date_method):
            parsed = date_method()
            if isinstance(parsed, date):
                return parsed
        raise ValueError("Tencent date must be an ISO date")


class JsonDailyMarketDataAdapter:
    """Offline replay through the same provider interface as the live adapter."""

    def __init__(self, fixture_path: str | Path) -> None:
        self._fixture_path = Path(fixture_path)
        self.source = "offline_fixture"

    def fetch(
        self,
        query: DailyBarQuery,
        policy: MarketDataFetchPolicy,
    ) -> MarketDataFetchResult:
        del policy
        try:
            payload = json.loads(self._fixture_path.read_text(encoding="utf-8"))
            if payload.get("dataset_type") not in {
                "captured_real_sample",
                "synthetic_fixture",
            }:
                raise ValueError("fixture dataset_type is not allowed")
            bars = [
                record
                for record in payload["bars"]
                if record["symbol"] == query.symbol
                and query.start_date
                <= datetime.fromisoformat(record["as_of"]).date()
                <= query.end_date
            ]
            series = MarketDataSeries.from_records(bars)
        except Exception as error:
            code = (
                MarketDataErrorCode.EMPTY_RESPONSE
                if isinstance(error, MarketDataValidationError)
                and "at least one" in str(error)
                else MarketDataErrorCode.FIXTURE_ERROR
            )
            raise MarketDataProviderError(
                "offline daily market-data fixture could not satisfy the query",
                code=code,
                source=self.source,
                attempts=1,
                cause=error,
            ) from error
        fetched_at = max(bar.timestamp for bar in series.bars)
        return MarketDataFetchResult(
            series=series,
            source=self.source,
            fetched_at=fetched_at,
            attempts=1,
            trace=(MarketDataTraceEvent("fixture.loaded", 1, self._fixture_path.name),),
        )


class DailyMarketDataTool:
    """Controlled Tool facade; callers do not depend on AKShare or JSON details."""

    name = "finance_daily_bars"

    def __init__(
        self,
        provider: DailyMarketDataProvider,
        *,
        policy: MarketDataFetchPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._policy = policy or MarketDataFetchPolicy()

    def run(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = DailyBarQuery.from_arguments(arguments)
        result = self._provider.fetch(query, self._policy)
        output = result.to_mapping()
        output["query"] = {
            "symbol": query.symbol,
            "start_date": query.start_date.isoformat(),
            "end_date": query.end_date.isoformat(),
            "adjust": query.adjust,
        }
        return output
