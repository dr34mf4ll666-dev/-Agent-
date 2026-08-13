"""Customer-facing financial analysis projection built on the complete C3 graph."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .core import (
    DeepSeekChatAdapter,
    ModelGateway,
    ModelGatewayConfigurationError,
    ModelRequest,
    ModelRetryPolicy,
)

from .finance import (
    C1DecisionQuery,
    CombinedAnalysisQuery,
    FinancialDataHub,
    FinancialDataPolicy,
    FinancialDataTool,
    FinancialGraphQuery,
    FixtureFinancialDataProvider,
    JsonFinancialDataCache,
    RiskContext,
    SubprocessFinancialDataProvider,
    build_default_financial_graph_runtime,
)


class ClientAnalysisError(ValueError):
    """A customer analysis request or projection is invalid."""


SECURITIES = {
    "sz000001": {
        "name": "平安银行",
        "exchange": "深交所",
        "sectors": {"offline": "玻璃行业", "live": "金融行业"},
    },
    "sh600000": {"name": "浦发银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh600015": {"name": "华夏银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh600016": {"name": "民生银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh600036": {"name": "招商银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601009": {"name": "南京银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601166": {"name": "兴业银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601169": {"name": "北京银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601229": {"name": "上海银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601288": {"name": "农业银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601328": {"name": "交通银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601398": {"name": "工商银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601658": {"name": "邮储银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601818": {"name": "光大银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601838": {"name": "成都银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601939": {"name": "建设银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601988": {"name": "中国银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sh601998": {"name": "中信银行", "exchange": "上交所", "sectors": {"live": "金融行业"}},
    "sz002142": {"name": "宁波银行", "exchange": "深交所", "sectors": {"live": "金融行业"}},
    "sz002807": {"name": "江阴银行", "exchange": "深交所", "sectors": {"live": "金融行业"}},
}


LABELS = {
    "strong_positive": "明显偏强",
    "positive": "偏强",
    "cautious_positive": "谨慎偏强",
    "neutral": "中性",
    "mixed": "多空交织",
    "negative": "偏弱",
    "strong_negative": "明显偏弱",
    "hot": "景气较高",
    "low": "风险偏好较低",
    "moderate": "风险偏好适中",
    "high": "风险偏好较高",
    "bullish": "趋势偏强",
    "bearish": "趋势偏弱",
    "buy": "偏多关注",
    "sell": "偏空回避",
    "hold": "继续观察",
    "reduce": "降低风险暴露",
    "approved": "通过风险检查",
    "adjusted": "调整后通过",
    "blocked": "风险阻断",
    "pending_human_confirmation": "等待人工确认",
    "forced_reduction": "需要降低风险",
    "no_action": "暂无动作",
}


@dataclass(frozen=True)
class ClientAnalysisRequest:
    symbol: str = "sz000001"
    mode: str = "offline"

    def __post_init__(self) -> None:
        if self.symbol not in SECURITIES:
            raise ClientAnalysisError("当前股票不在已验证的客户分析目录中。")
        if self.mode not in {"offline", "live"}:
            raise ClientAnalysisError("数据模式必须是 offline 或 live。")
        if self.mode not in SECURITIES[self.symbol]["sectors"]:
            raise ClientAnalysisError(
                f"{SECURITIES[self.symbol]['name']}当前只支持最新数据，"
                "没有可独立复现的离线全量样本。"
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ClientAnalysisRequest":
        if not isinstance(value, Mapping):
            raise ClientAnalysisError("分析请求必须是对象。")
        return cls(
            symbol=str(value.get("symbol", "sz000001")).lower().strip(),
            mode=str(value.get("mode", "offline")).lower().strip(),
        )


class FinancialGraphPort(Protocol):
    def run(self, query: FinancialGraphQuery) -> Mapping[str, Any]:
        """Run C3 and return its complete mapping."""


class DefaultFinancialGraphAdapter:
    """Production adapter that keeps C3 construction behind the client seam."""

    def __init__(self, project_root: Path, policy: FinancialDataPolicy) -> None:
        self._project_root = project_root
        self._policy = policy

    def run(self, query: FinancialGraphQuery) -> Mapping[str, Any]:
        return build_default_financial_graph_runtime(
            project_root=self._project_root,
            policy=self._policy,
        ).run(query).to_mapping()


@dataclass(frozen=True)
class ClientAnalysisResult:
    value: Mapping[str, Any]
    debate_context: Mapping[str, Any] | None = None

    def to_mapping(self) -> dict[str, Any]:
        return dict(self.value)


class ClientAnalysisRuntime:
    """Deep customer interface: one request becomes one presentation-ready report."""

    def __init__(
        self,
        *,
        graph: FinancialGraphPort,
        market_tool: FinancialDataTool,
        now: Any | None = None,
    ) -> None:
        self._graph = graph
        self._market_tool = market_tool
        self._now = now or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))

    @classmethod
    def from_project(
        cls,
        project_root: str | Path | None = None,
        *,
        policy: FinancialDataPolicy | None = None,
    ) -> "ClientAnalysisRuntime":
        root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        active_policy = policy or FinancialDataPolicy(
            timeout_seconds=60.0,
            max_attempts=1,
        )
        market_hub = FinancialDataHub(
            live_provider=SubprocessFinancialDataProvider(),
            offline_provider=FixtureFinancialDataProvider(
                root / "tests" / "fixtures" / "technical_market_daily_30.json"
            ),
            cache=JsonFinancialDataCache(
                root / ".runtime" / "finance" / "client_chart_cache.json"
            ),
            policy=active_policy,
        )
        return cls(
            graph=DefaultFinancialGraphAdapter(root, active_policy),
            market_tool=FinancialDataTool(market_hub),
        )

    def analyze(self, request: ClientAnalysisRequest) -> ClientAnalysisResult:
        security = SECURITIES[request.symbol]
        sector = security["sectors"][request.mode]
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ClientAnalysisError("分析时钟必须包含时区。")
        evaluation_time = (
            "2026-08-07T10:00:00+08:00"
            if request.mode == "offline"
            else now.isoformat(timespec="seconds")
        )
        query = FinancialGraphQuery(
            c1_query=C1DecisionQuery(
                combined_query=CombinedAnalysisQuery.for_symbol(
                    symbol=request.symbol,
                    sector=sector,
                    index_symbol="sh000300",
                    mode=request.mode,
                    start_date="20240101",
                    end_date=(
                        "20260807"
                        if request.mode == "offline"
                        else now.strftime("%Y%m%d")
                    ),
                ),
                debate_rounds=2,
                base_position_cap_percent=30,
            ),
            risk_context=RiskContext(
                account_equity="100000",
                current_position_percent="0",
                requested_position_percent="15",
                sector_exposure_other_percent="5",
                current_drawdown_percent="5",
                average_daily_turnover="500000000",
                evaluation_time=evaluation_time,
                human_confirmed=False,
            ),
        )
        graph_result = self._graph.run(query)
        chart_output = self._market_tool.run(
            {
                "dataset": "market.daily",
                "params": {
                    "symbol": request.symbol,
                    "start_date": "20260626" if request.mode == "offline" else _days_ago(now, 120),
                    "end_date": "20260806" if request.mode == "offline" else now.strftime("%Y%m%d"),
                    "limit": 60,
                },
                "mode": request.mode,
            }
        )
        projected = _project_for_customer(
            graph_result,
            chart_output,
            name=str(security["name"]),
            exchange=str(security["exchange"]),
            data_note=(
                "已验证历史快照"
                if request.mode == "offline"
                else "最新只读市场数据"
            ),
        )
        try:
            debate_context = graph_result["report"]["research"]["report"]["combined_analysis"]
        except (KeyError, TypeError) as error:
            raise ClientAnalysisError(f"C3 报告缺少动态辩论上下文: {error}") from error
        return ClientAnalysisResult(projected, debate_context=debate_context)


def _days_ago(value: datetime, days: int) -> str:
    return (value.date() - timedelta(days=days)).strftime("%Y%m%d")


def _label(value: Any) -> str:
    return LABELS.get(str(value), str(value))


def _percent_from_ratio(value: Any) -> str:
    try:
        return f"{(Decimal(str(value)) * Decimal('100')).quantize(Decimal('0.01'))}"
    except (InvalidOperation, ValueError) as error:
        raise ClientAnalysisError("收益率字段无法转换。") from error


def _market_bars(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    records = value.get("records", [])
    if not isinstance(records, list):
        raise ClientAnalysisError("行情记录格式无效。")
    for record in records[-60:]:
        fields = record.get("fields", {})
        result.append(
            {
                "date": str(record["as_of"])[:10],
                "open": str(fields["open"]),
                "high": str(fields["high"]),
                "low": str(fields["low"]),
                "close": str(fields["close"]),
                "volume": str(fields.get("volume_shares", "0")),
            }
        )
    if not result:
        raise ClientAnalysisError("没有可展示的行情记录。")
    return result


def _project_for_customer(
    graph_result: Mapping[str, Any],
    chart_output: Mapping[str, Any],
    *,
    name: str,
    exchange: str,
    data_note: str,
) -> dict[str, Any]:
    try:
        report = graph_result["report"]
        research = report["research"]["report"]
        combined = research["combined_analysis"]
        specialists = combined["reports"]
        technical = specialists["technical"]
        fundamental = specialists["fundamental"]
        industry = specialists["industry"]
        macro = specialists["macro"]
        synthesis = research["synthesis"]
        debate = research["debate"]
        trader = report["trader"]["report"]
        risk_wrapper = report.get("risk_manager")
        risk = risk_wrapper["report"] if risk_wrapper else None
        interval = synthesis["target_price_interval"]
        first_round = debate["rounds"][0]
    except (KeyError, IndexError, TypeError) as error:
        raise ClientAnalysisError(f"C3 报告缺少客户展示字段: {error}") from error

    market_return = _percent_from_ratio(technical["daily_return"])
    dimensions = [
        {
            "id": "technical",
            "name": "趋势走势",
            "caption": "价格与动量",
            "score": technical["signal_score"],
            "label": _label(technical["signal_label"]),
            "summary": (
                f"最新价 {technical['latest_close']}，RSI14 为 {technical['rsi']['rsi_14']}；"
                f"短中期均线呈{_label(technical['trend'])}状态。"
            ),
        },
        {
            "id": "fundamental",
            "name": "经营质量",
            "caption": "财务与估值",
            "score": fundamental["score"],
            "label": _label(fundamental["score_label"]),
            "summary": (
                f"动态 PE {fundamental['valuation']['pe_dynamic']}，PB {fundamental['valuation']['pb']}；"
                f"净利润增长 {fundamental['growth']['net_profit_growth_percent']}%。"
            ),
        },
        {
            "id": "industry",
            "name": "行业温度",
            "caption": "景气与政策",
            "score": industry["score"],
            "label": _label(industry["score_label"]),
            "summary": (
                f"行业景气度为{_label(industry['prosperity']['label'])}，"
                f"行业样本涨跌 {industry['prosperity']['sector_change_percent']}%。"
            ),
            "sample_scope": industry["sector"],
        },
        {
            "id": "macro",
            "name": "市场环境",
            "caption": "指数、资金与宏观",
            "score": macro["score"],
            "label": _label(macro["score_label"]),
            "summary": (
                f"市场状态为{_label(macro['market_regime']['label'])}，"
                f"风险偏好{_label(macro['risk_appetite']['label'])}。"
            ),
        },
    ]
    risk_view = {
        "position_cap_percent": synthesis["market_regime_gate"][
            "effective_position_cap_percent"
        ],
        "stop_loss": interval["lower"],
        "take_profit": interval["upper"],
        "estimated_loss_percent": None,
        "reward_risk_ratio": None,
        "status": _label(report["final_decision"]["status"]),
    }


    if risk is not None:
        risk_view.update(
            {
                "estimated_loss_percent": risk["position"][
                    "estimated_single_trade_loss_percent"
                ],
                "reward_risk_ratio": risk["price_controls"]["reward_risk_ratio"],
                "status": _label(risk["risk_decision"]["status"]),
            }
        )

    all_sources = list(dict.fromkeys(combined["sources"]))
    return {
        "status": "succeeded",
        "security": {
            "symbol": report["symbol"],
            "code": report["symbol"][2:],
            "name": name,
            "exchange": exchange,
        },
        "data": {
            "mode": report["mode"],
            "label": data_note,
            "as_of": technical["as_of"],
            "timestamp": technical["timestamp"],
            "source_count": len(all_sources),
            "sources": all_sources,
            "bars": _market_bars(chart_output),
        },
        "quote": {
            "latest_close": technical["latest_close"],
            "daily_return_percent": market_return,
            "support": technical["levels"]["support_20"],
            "resistance": technical["levels"]["resistance_20"],
        },
        "verdict": {
            "inclination": synthesis["inclination"],
            "label": _label(synthesis["inclination"]),
            "weighted_score": synthesis["weighted_score"],
            "confidence": synthesis["confidence"],
            "confidence_note": "证据一致性，不代表上涨概率",
            "action": trader["signal"]["action"],
            "action_label": _label(trader["signal"]["action"]),
        },
        "price_band": {
            "lower": interval["lower"],
            "reference": interval["reference"],
            "upper": interval["upper"],
            "note": "确定性研究区间，不是价格预测",
        },
        "dimensions": dimensions,
        "debate": {
            "positive": first_round["bull"]["claim"],
            "positive_reasoning": first_round["bull"]["reasoning"],
            "risk": first_round["bear"]["claim"],
            "risk_reasoning": first_round["bear"]["reasoning"],
            "rounds": len(debate["rounds"]),
        },
        "risk": risk_view,
        "quality": {
            "consistency": research["quality"]["consistency_check"]["status"],
            "bias": research["quality"]["bias_detector"]["status"],
        },
        "safety": {
            "simulation_only": True,
            "order_created": False,
            "real_trading_allowed": False,
            "notice": "仅供研究与教学，不构成投资建议。",
        },
    }


class MarketAssistant(Protocol):
    provider: str
    model: str
    live: bool

    def explain(self, analysis: Mapping[str, Any]) -> dict[str, Any]:
        """Explain an already-computed analysis without changing it."""


class LocalMarketAssistant:
    provider = "local"
    model = "deterministic-market-guide"
    live = False

    def explain(self, analysis: Mapping[str, Any]) -> dict[str, Any]:
        verdict = analysis["verdict"]
        dimensions = analysis["dimensions"]
        strongest = max(dimensions, key=lambda item: int(item["score"]))
        weakest = min(dimensions, key=lambda item: int(item["score"]))
        return {
            "headline": f"研究观点为{verdict['label']}，但四个维度并不完全一致",
            "explanation": (
                f"当前更有支撑的维度是{strongest['name']}（{strongest['score']}分），"
                f"相对需要留意的是{weakest['name']}（{weakest['score']}分）。"
                f"证据一致性为 {verdict['confidence']}/100，它衡量资料之间是否相互支持，"
                "不是上涨概率。"
            ),
            "risk_note": (
                f"研究区间下沿为 {analysis['price_band']['lower']}，上沿为 "
                f"{analysis['price_band']['upper']}；这些数字用于解释风险边界，不是收益承诺。"
            ),
            "provider": self.provider,
            "model": self.model,
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "latency_ms": 0,
        }


class DeepSeekMarketAssistant:
    provider = "deepseek"
    live = True

    def __init__(self, gateway: ModelGateway, *, model: str) -> None:
        self._gateway = gateway
        self.model = model

    @classmethod
    def from_env(cls) -> "DeepSeekMarketAssistant":
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        gateway = ModelGateway(
            DeepSeekChatAdapter.from_env(model=model),
            retry_policy=ModelRetryPolicy(
                max_attempts=2,
                timeout_seconds=30,
                initial_backoff_seconds=0.25,
            ),
        )
        return cls(gateway, model=model)

    def explain(self, analysis: Mapping[str, Any]) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "headline": {"type": "string", "minLength": 1},
                "explanation": {"type": "string", "minLength": 1},
                "risk_note": {"type": "string", "minLength": 1},
            },
            "required": ["headline", "explanation", "risk_note"],
            "additionalProperties": False,
        }
        context = {
            "security": analysis.get("security"),
            "quote": analysis.get("quote"),
            "verdict": analysis.get("verdict"),
            "price_band": analysis.get("price_band"),
            "dimensions": analysis.get("dimensions"),
            "debate": analysis.get("debate"),
            "risk": analysis.get("risk"),
            "data": {
                key: analysis.get("data", {}).get(key)
                for key in ("label", "as_of", "source_count")
            },
        }
        result = self._gateway.generate(
            ModelRequest(
                prompt=json.dumps(context, ensure_ascii=False),
                system_prompt=(
                    "你是面向普通用户的证券研究解读助手。只解释给定的确定性分析，使用简明中文；"
                    "不能改写分数、价格、仓位或风险结论，不能把一致性置信度说成上涨概率，"
                    "不能承诺收益或声称已经交易。headline 一句话，explanation 两到三句，"
                    "risk_note 一到两句。"
                ),
                response_schema=schema,
                schema_name="client_market_explanation",
                max_output_tokens=420,
            )
        )
        output = dict(result.response.structured_output)
        output.update(
            {
                "provider": result.response.provider,
                "model": result.response.model,
                "usage": {
                    "input_tokens": result.response.usage.input_tokens,
                    "output_tokens": result.response.usage.output_tokens,
                    "total_tokens": result.response.usage.total_tokens,
                },
                "latency_ms": result.response.latency_ms,
            }
        )
        return output


def build_default_market_assistant() -> MarketAssistant:
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return LocalMarketAssistant()
    try:
        return DeepSeekMarketAssistant.from_env()
    except ModelGatewayConfigurationError:
        return LocalMarketAssistant()


__all__ = [
    "ClientAnalysisError",
    "ClientAnalysisRequest",
    "ClientAnalysisResult",
    "ClientAnalysisRuntime",
    "DefaultFinancialGraphAdapter",
    "DeepSeekMarketAssistant",
    "FinancialGraphPort",
    "LocalMarketAssistant",
    "MarketAssistant",
    "SECURITIES",
    "build_default_market_assistant",
]
