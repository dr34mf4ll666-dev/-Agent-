"""不依赖 LLM 的确定性技术分析 Agent。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from agent_platform.core import AgentRequest, AgentResponse

from .contracts import MarketDataSeries


class TechnicalAnalysisError(ValueError):
    """技术分析输入不满足确定性计算约束。"""


class InsufficientMarketDataError(TechnicalAnalysisError):
    """行情数量不足以计算最长窗口指标。"""

    def __init__(self, *, required: int, actual: int) -> None:
        super().__init__(
            f"technical analysis requires at least {required} bars; got {actual}"
        )
        self.required = required
        self.actual = actual


@dataclass(frozen=True)
class TechnicalAnalysisResult:
    """技术指标和简化趋势分类的结构化结果。"""

    symbol: str
    as_of: datetime
    sample_size: int
    latest_close: Decimal
    daily_return: Decimal
    sma_5: Decimal
    sma_20: Decimal
    trend: str
    trend_rule: str
    sources: tuple[str, ...]

    def to_metadata(self) -> dict[str, object]:
        """转换为 JSON 兼容的 AgentResponse 元数据。"""

        return {
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "sample_size": self.sample_size,
            "latest_close": str(self.latest_close),
            "daily_return": str(self.daily_return),
            "sma_5": str(self.sma_5),
            "sma_20": str(self.sma_20),
            "trend": self.trend,
            "trend_rule": self.trend_rule,
            "sources": list(self.sources),
        }


class TechnicalAnalysisAgent:
    """从经过校验的行情序列计算固定技术指标。"""

    name = "technical_analysis"
    short_window = 5
    long_window = 20

    def run(self, request: AgentRequest) -> AgentResponse:
        series = request.context.get("market_data")
        if not isinstance(series, MarketDataSeries):
            raise TechnicalAnalysisError(
                "request.context['market_data'] must be a MarketDataSeries"
            )
        if len(series.bars) < self.long_window:
            raise InsufficientMarketDataError(
                required=self.long_window,
                actual=len(series.bars),
            )
        closes = tuple(bar.close for bar in series.bars)
        latest_close = closes[-1]
        daily_return = latest_close / closes[-2] - Decimal("1")
        sma_5 = self._simple_moving_average(closes, self.short_window)
        sma_20 = self._simple_moving_average(closes, self.long_window)
        trend, trend_rule = self._classify_trend(latest_close, sma_5, sma_20)

        result = TechnicalAnalysisResult(
            symbol=series.symbol,
            as_of=series.bars[-1].as_of,
            sample_size=len(series.bars),
            latest_close=latest_close,
            daily_return=daily_return,
            sma_5=sma_5,
            sma_20=sma_20,
            trend=trend,
            trend_rule=trend_rule,
            sources=tuple(sorted({bar.source for bar in series.bars})),
        )
        return AgentResponse(
            content=(
                f"{result.symbol} 截至 {result.as_of.isoformat()} 的技术状态为 "
                f"{result.trend}；收盘价 {result.latest_close}，"
                f"SMA5 {result.sma_5}，SMA20 {result.sma_20}。"
                "该结果来自确定性规则，不构成投资建议。"
            ),
            metadata={"agent": self.name, "analysis": result.to_metadata()},
        )

    @staticmethod
    def _simple_moving_average(
        closes: tuple[Decimal, ...],
        window: int,
    ) -> Decimal:
        return sum(closes[-window:], start=Decimal("0")) / Decimal(window)

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
