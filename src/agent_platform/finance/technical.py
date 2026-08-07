"""Deterministic technical analysis shared by Agent, Loop, and Graph adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from agent_platform.core import AgentRequest, AgentResponse

from .contracts import MarketDataSeries


class TechnicalAnalysisError(ValueError):
    """Technical-analysis input does not satisfy deterministic constraints."""


class InsufficientMarketDataError(TechnicalAnalysisError):
    """The series is too short for the longest required calculation."""

    def __init__(self, *, required: int, actual: int) -> None:
        super().__init__(
            f"technical analysis requires at least {required} bars; got {actual}"
        )
        self.required = required
        self.actual = actual


@dataclass(frozen=True)
class TechnicalSignalComponent:
    """One auditable rule contribution to the final signal score."""

    name: str
    points: int
    rule: str

    def to_mapping(self) -> dict[str, object]:
        return {"name": self.name, "points": self.points, "rule": self.rule}


@dataclass(frozen=True)
class TechnicalAnalysisResult:
    """Complete deterministic indicator snapshot for one security."""

    symbol: str
    as_of: datetime
    timestamp: datetime
    sample_size: int
    latest_close: Decimal
    daily_return: Decimal
    sma_5: Decimal
    sma_10: Decimal
    sma_20: Decimal
    macd_dif: Decimal
    macd_dea: Decimal
    macd_histogram: Decimal
    rsi_14: Decimal
    kdj_k: Decimal
    kdj_d: Decimal
    kdj_j: Decimal
    boll_middle: Decimal
    boll_upper: Decimal
    boll_lower: Decimal
    support_20: Decimal
    resistance_20: Decimal
    distance_to_support: Decimal
    distance_to_resistance: Decimal
    trend: str
    trend_rule: str
    signal_score: int
    signal_label: str
    score_components: tuple[TechnicalSignalComponent, ...]
    sources: tuple[str, ...]

    def to_metadata(self) -> dict[str, object]:
        """Convert the result to stable JSON-compatible metadata."""

        return {
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "timestamp": self.timestamp.isoformat(),
            "sample_size": self.sample_size,
            "latest_close": str(self.latest_close),
            "daily_return": str(self.daily_return),
            "ma": {
                "sma_5": str(self.sma_5),
                "sma_10": str(self.sma_10),
                "sma_20": str(self.sma_20),
            },
            "macd": {
                "dif": str(self.macd_dif),
                "dea": str(self.macd_dea),
                "histogram": str(self.macd_histogram),
            },
            "rsi": {"rsi_14": str(self.rsi_14)},
            "kdj": {
                "k": str(self.kdj_k),
                "d": str(self.kdj_d),
                "j": str(self.kdj_j),
            },
            "bollinger": {
                "middle": str(self.boll_middle),
                "upper": str(self.boll_upper),
                "lower": str(self.boll_lower),
            },
            "levels": {
                "support_20": str(self.support_20),
                "resistance_20": str(self.resistance_20),
                "distance_to_support": str(self.distance_to_support),
                "distance_to_resistance": str(self.distance_to_resistance),
            },
            "trend": self.trend,
            "trend_rule": self.trend_rule,
            "signal_score": self.signal_score,
            "signal_label": self.signal_label,
            "score_components": [
                component.to_mapping() for component in self.score_components
            ],
            "sources": list(self.sources),
        }


class TechnicalAnalysisEngine:
    """Deep pure-calculation module behind one ``analyze(series)`` interface."""

    minimum_bars = 30

    def analyze(self, series: MarketDataSeries) -> TechnicalAnalysisResult:
        if not isinstance(series, MarketDataSeries):
            raise TechnicalAnalysisError("series must be a MarketDataSeries")
        if len(series.bars) < self.minimum_bars:
            raise InsufficientMarketDataError(
                required=self.minimum_bars,
                actual=len(series.bars),
            )

        closes = tuple(bar.close for bar in series.bars)
        highs = tuple(bar.high for bar in series.bars)
        lows = tuple(bar.low for bar in series.bars)
        latest_close = closes[-1]
        daily_return = latest_close / closes[-2] - Decimal("1")
        sma_5 = self._sma(closes, 5)
        sma_10 = self._sma(closes, 10)
        sma_20 = self._sma(closes, 20)
        dif, dea, histogram = self._macd(closes)
        rsi_14 = self._rsi(closes, 14)
        kdj_k, kdj_d, kdj_j = self._kdj(highs, lows, closes, 9)
        boll_middle, boll_upper, boll_lower = self._bollinger(closes, 20)
        support_20 = min(lows[-20:])
        resistance_20 = max(highs[-20:])
        distance_to_support = latest_close / support_20 - Decimal("1")
        distance_to_resistance = resistance_20 / latest_close - Decimal("1")
        trend, trend_rule = self._classify_trend(latest_close, sma_5, sma_20)
        components = self._score_components(
            latest_close=latest_close,
            sma_5=sma_5,
            sma_20=sma_20,
            macd_histogram=histogram,
            rsi_14=rsi_14,
            kdj_k=kdj_k,
            kdj_d=kdj_d,
            kdj_j=kdj_j,
            boll_upper=boll_upper,
            boll_lower=boll_lower,
            distance_to_support=distance_to_support,
            distance_to_resistance=distance_to_resistance,
        )
        signal_score = sum(component.points for component in components)

        return TechnicalAnalysisResult(
            symbol=series.symbol,
            as_of=series.bars[-1].as_of,
            timestamp=max(bar.timestamp for bar in series.bars),
            sample_size=len(series.bars),
            latest_close=self._price(latest_close),
            daily_return=self._ratio(daily_return),
            sma_5=self._price(sma_5),
            sma_10=self._price(sma_10),
            sma_20=self._price(sma_20),
            macd_dif=self._price(dif),
            macd_dea=self._price(dea),
            macd_histogram=self._price(histogram),
            rsi_14=self._price(rsi_14),
            kdj_k=self._price(kdj_k),
            kdj_d=self._price(kdj_d),
            kdj_j=self._price(kdj_j),
            boll_middle=self._price(boll_middle),
            boll_upper=self._price(boll_upper),
            boll_lower=self._price(boll_lower),
            support_20=self._price(support_20),
            resistance_20=self._price(resistance_20),
            distance_to_support=self._ratio(distance_to_support),
            distance_to_resistance=self._ratio(distance_to_resistance),
            trend=trend,
            trend_rule=trend_rule,
            signal_score=signal_score,
            signal_label=self._signal_label(signal_score),
            score_components=components,
            sources=tuple(sorted({bar.source for bar in series.bars})),
        )

    @staticmethod
    def _sma(values: tuple[Decimal, ...], window: int) -> Decimal:
        return sum(values[-window:], start=Decimal("0")) / Decimal(window)

    @staticmethod
    def _ema_series(values: tuple[Decimal, ...], period: int) -> tuple[Decimal, ...]:
        alpha = Decimal("2") / Decimal(period + 1)
        result = [values[0]]
        for value in values[1:]:
            result.append(alpha * value + (Decimal("1") - alpha) * result[-1])
        return tuple(result)

    @classmethod
    def _macd(
        cls,
        closes: tuple[Decimal, ...],
    ) -> tuple[Decimal, Decimal, Decimal]:
        ema_12 = cls._ema_series(closes, 12)
        ema_26 = cls._ema_series(closes, 26)
        dif_series = tuple(short - long for short, long in zip(ema_12, ema_26))
        dea_series = cls._ema_series(dif_series, 9)
        dif = dif_series[-1]
        dea = dea_series[-1]
        return dif, dea, (dif - dea) * Decimal("2")

    @staticmethod
    def _rsi(closes: tuple[Decimal, ...], period: int) -> Decimal:
        changes = tuple(current - previous for previous, current in zip(closes, closes[1:]))
        gains = tuple(max(change, Decimal("0")) for change in changes)
        losses = tuple(max(-change, Decimal("0")) for change in changes)
        average_gain = sum(gains[:period], Decimal("0")) / Decimal(period)
        average_loss = sum(losses[:period], Decimal("0")) / Decimal(period)
        for gain, loss in zip(gains[period:], losses[period:]):
            average_gain = (
                average_gain * Decimal(period - 1) + gain
            ) / Decimal(period)
            average_loss = (
                average_loss * Decimal(period - 1) + loss
            ) / Decimal(period)
        if average_loss == 0:
            return Decimal("100") if average_gain > 0 else Decimal("50")
        relative_strength = average_gain / average_loss
        return Decimal("100") - Decimal("100") / (Decimal("1") + relative_strength)

    @staticmethod
    def _kdj(
        highs: tuple[Decimal, ...],
        lows: tuple[Decimal, ...],
        closes: tuple[Decimal, ...],
        period: int,
    ) -> tuple[Decimal, Decimal, Decimal]:
        k = Decimal("50")
        d = Decimal("50")
        for index in range(period - 1, len(closes)):
            lowest = min(lows[index - period + 1 : index + 1])
            highest = max(highs[index - period + 1 : index + 1])
            rsv = (
                Decimal("50")
                if highest == lowest
                else (closes[index] - lowest) / (highest - lowest) * Decimal("100")
            )
            k = Decimal("2") / Decimal("3") * k + Decimal("1") / Decimal("3") * rsv
            d = Decimal("2") / Decimal("3") * d + Decimal("1") / Decimal("3") * k
        return k, d, Decimal("3") * k - Decimal("2") * d

    @classmethod
    def _bollinger(
        cls,
        closes: tuple[Decimal, ...],
        period: int,
    ) -> tuple[Decimal, Decimal, Decimal]:
        window = closes[-period:]
        middle = cls._sma(closes, period)
        variance = sum(
            ((value - middle) ** 2 for value in window),
            Decimal("0"),
        ) / Decimal(period)
        standard_deviation = variance.sqrt()
        return (
            middle,
            middle + Decimal("2") * standard_deviation,
            middle - Decimal("2") * standard_deviation,
        )

    @staticmethod
    def _classify_trend(
        latest_close: Decimal,
        sma_5: Decimal,
        sma_20: Decimal,
    ) -> tuple[str, str]:
        if latest_close > sma_5 > sma_20:
            return "bullish", "latest_close > sma_5 > sma_20"
        if latest_close < sma_5 < sma_20:
            return "bearish", "latest_close < sma_5 < sma_20"
        return "mixed", "moving averages are not strictly aligned"

    @staticmethod
    def _score_components(**values: Decimal) -> tuple[TechnicalSignalComponent, ...]:
        close = values["latest_close"]
        sma_5 = values["sma_5"]
        sma_20 = values["sma_20"]
        if close > sma_5 > sma_20:
            trend_points, trend_rule = 20, "close > SMA5 > SMA20"
        elif close < sma_5 < sma_20:
            trend_points, trend_rule = -20, "close < SMA5 < SMA20"
        else:
            trend_points, trend_rule = 0, "moving averages are mixed"

        macd_points = 15 if values["macd_histogram"] > 0 else -15
        macd_rule = "MACD histogram > 0" if macd_points > 0 else "MACD histogram <= 0"

        rsi = values["rsi_14"]
        if rsi < 30:
            rsi_points, rsi_rule = 15, "RSI14 < 30 (oversold)"
        elif rsi > 70:
            rsi_points, rsi_rule = -15, "RSI14 > 70 (overbought)"
        else:
            rsi_points, rsi_rule = 0, "30 <= RSI14 <= 70"

        if values["kdj_j"] < 20:
            kdj_points, kdj_rule = 10, "KDJ J < 20"
        elif values["kdj_j"] > 80:
            kdj_points, kdj_rule = -10, "KDJ J > 80"
        elif values["kdj_k"] > values["kdj_d"]:
            kdj_points, kdj_rule = 5, "KDJ K > D"
        else:
            kdj_points, kdj_rule = -5, "KDJ K <= D"

        if close < values["boll_lower"]:
            boll_points, boll_rule = 10, "close < lower Bollinger band"
        elif close > values["boll_upper"]:
            boll_points, boll_rule = -10, "close > upper Bollinger band"
        else:
            boll_points, boll_rule = 0, "close is inside Bollinger bands"

        support_points = (
            10
            if Decimal("0") <= values["distance_to_support"] <= Decimal("0.02")
            else 0
        )
        resistance_points = (
            -10
            if Decimal("0") <= values["distance_to_resistance"] <= Decimal("0.02")
            else 0
        )
        return (
            TechnicalSignalComponent("trend", trend_points, trend_rule),
            TechnicalSignalComponent("macd", macd_points, macd_rule),
            TechnicalSignalComponent("rsi", rsi_points, rsi_rule),
            TechnicalSignalComponent("kdj", kdj_points, kdj_rule),
            TechnicalSignalComponent("bollinger", boll_points, boll_rule),
            TechnicalSignalComponent(
                "support",
                support_points,
                "within 2% above support" if support_points else "not near support",
            ),
            TechnicalSignalComponent(
                "resistance",
                resistance_points,
                "within 2% below resistance" if resistance_points else "not near resistance",
            ),
        )

    @staticmethod
    def _signal_label(score: int) -> str:
        if score >= 40:
            return "strong_bullish"
        if score >= 15:
            return "bullish"
        if score > -15:
            return "neutral"
        if score > -40:
            return "bearish"
        return "strong_bearish"

    @staticmethod
    def _price(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.0001"))

    @staticmethod
    def _ratio(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.000001"))


class TechnicalAnalysisAgent:
    """Harness-compatible deterministic Agent over validated market data."""

    name = "technical_analysis"

    def __init__(self, engine: TechnicalAnalysisEngine | None = None) -> None:
        self._engine = engine or TechnicalAnalysisEngine()

    def run(self, request: AgentRequest) -> AgentResponse:
        series = request.context.get("market_data")
        if not isinstance(series, MarketDataSeries):
            raise TechnicalAnalysisError(
                "request.context['market_data'] must be a MarketDataSeries"
            )
        result = self._engine.analyze(series)
        return AgentResponse(
            content=(
                f"{result.symbol} 截至 {result.as_of.isoformat()} 的技术状态为 "
                f"{result.trend}，综合评分 {result.signal_score} "
                f"({result.signal_label})；收盘价 {result.latest_close}。"
                "所有指标由确定性代码计算，不构成投资建议。"
            ),
            metadata={"agent": self.name, "analysis": result.to_metadata()},
        )


__all__ = [
    "InsufficientMarketDataError",
    "TechnicalAnalysisAgent",
    "TechnicalAnalysisEngine",
    "TechnicalAnalysisError",
    "TechnicalAnalysisResult",
    "TechnicalSignalComponent",
]
