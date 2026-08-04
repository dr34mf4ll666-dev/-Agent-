"""可追溯的金融行情数据契约。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


class MarketDataValidationError(ValueError):
    """行情记录或时间序列不满足金融数据契约。"""


def _parse_decimal(value: Any, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise MarketDataValidationError(
            f"{field_name} must be a valid decimal number"
        ) from error


def _parse_volume(value: Any) -> int:
    parsed = _parse_decimal(value, "volume")
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise MarketDataValidationError("volume must be a whole number")
    return int(parsed)


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise MarketDataValidationError(
            f"{field_name} must be an ISO 8601 datetime"
        )
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise MarketDataValidationError(
            f"{field_name} must be an ISO 8601 datetime"
        ) from error


@dataclass(frozen=True)
class MarketBar:
    """一根带来源与时间语义的 OHLCV K 线。"""

    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    source: str
    timestamp: datetime
    as_of: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise MarketDataValidationError("symbol must be a non-empty string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise MarketDataValidationError("source must be a non-empty string")
        object.__setattr__(self, "symbol", self.symbol.strip())
        object.__setattr__(self, "source", self.source.strip())

        for field_name in ("open", "high", "low", "close"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                raise MarketDataValidationError(
                    f"{field_name} must be provided as Decimal"
                )
            if not value.is_finite() or value <= 0:
                raise MarketDataValidationError(
                    f"{field_name} must be a finite positive price"
                )
        if not isinstance(self.volume, int) or isinstance(self.volume, bool):
            raise MarketDataValidationError("volume must be a whole number")
        if self.volume < 0:
            raise MarketDataValidationError("volume must be greater than or equal to zero")
        if self.high < max(self.open, self.low, self.close):
            raise MarketDataValidationError(
                "high must be greater than or equal to open, low, and close"
            )
        if self.low > min(self.open, self.high, self.close):
            raise MarketDataValidationError(
                "low must be less than or equal to open, high, and close"
            )
        if not isinstance(self.timestamp, datetime):
            raise MarketDataValidationError("timestamp must be a datetime")
        if not isinstance(self.as_of, datetime):
            raise MarketDataValidationError("as_of must be a datetime")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise MarketDataValidationError("timestamp must include a timezone")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise MarketDataValidationError("as_of must include a timezone")
        if self.as_of > self.timestamp:
            raise MarketDataValidationError("as_of must not be later than timestamp")

    @classmethod
    def from_mapping(cls, record: Mapping[str, Any]) -> "MarketBar":
        try:
            return cls(
                symbol=record["symbol"],
                open=_parse_decimal(record["open"], "open"),
                high=_parse_decimal(record["high"], "high"),
                low=_parse_decimal(record["low"], "low"),
                close=_parse_decimal(record["close"], "close"),
                volume=_parse_volume(record["volume"]),
                source=record["source"],
                timestamp=_parse_datetime(record["timestamp"], "timestamp"),
                as_of=_parse_datetime(record["as_of"], "as_of"),
            )
        except KeyError as error:
            raise MarketDataValidationError(
                f"missing required field: {error.args[0]}"
            ) from error


@dataclass(frozen=True)
class MarketDataSeries:
    """同一证券按 as_of 排列的 K 线序列。"""

    bars: tuple[MarketBar, ...]

    def __post_init__(self) -> None:
        if not self.bars:
            raise MarketDataValidationError(
                "market data series must contain at least one bar"
            )
        if len({bar.symbol for bar in self.bars}) != 1:
            raise MarketDataValidationError(
                "market data series must contain exactly one symbol"
            )
        if any(
            current.as_of >= following.as_of
            for current, following in zip(self.bars, self.bars[1:])
        ):
            raise MarketDataValidationError(
                "market data series as_of values must be strictly increasing"
            )

    @classmethod
    def from_records(
        cls,
        records: Iterable[Mapping[str, Any]],
    ) -> "MarketDataSeries":
        return cls(tuple(MarketBar.from_mapping(record) for record in records))

    @property
    def symbol(self) -> str:
        return self.bars[0].symbol
