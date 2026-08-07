"""Run the B2 technical specialist through Data Hub, Loop, and Harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    FinancialDataPolicy,
    TechnicalAnalysisQuery,
    build_default_technical_analysis_runtime,
)


TREND_LABELS = {
    "bullish": "看多（bullish）",
    "bearish": "看空（bearish）",
    "mixed": "混合（mixed）",
}

COMPONENT_LABELS = {
    "trend": "趋势（trend）",
    "macd": "指数平滑异同移动平均线（MACD）",
    "rsi": "相对强弱指标（RSI）",
    "kdj": "随机指标（KDJ）",
    "bollinger": "布林带（Bollinger）",
    "support": "支撑位（support）",
    "resistance": "阻力位（resistance）",
}

RULE_LABELS = {
    "moving averages are mixed": "均线排列混合（moving averages are mixed）",
    "MACD histogram > 0": "MACD 柱体大于 0（MACD histogram > 0）",
    "MACD histogram <= 0": "MACD 柱体小于等于 0（MACD histogram <= 0）",
    "30 <= RSI14 <= 70": "RSI14 处于 30 到 70 之间（30 <= RSI14 <= 70）",
    "RSI14 < 30 (oversold)": "RSI14 小于 30，可能超卖（RSI14 < 30）",
    "RSI14 > 70 (overbought)": "RSI14 大于 70，可能超买（RSI14 > 70）",
    "KDJ J < 20": "KDJ 的 J 值小于 20（KDJ J < 20）",
    "KDJ J > 80": "KDJ 的 J 值大于 80（KDJ J > 80）",
    "KDJ K > D": "KDJ 的 K 值大于 D 值（KDJ K > D）",
    "KDJ K <= D": "KDJ 的 K 值小于等于 D 值（KDJ K <= D）",
    "close < lower Bollinger band": "收盘价低于布林带下轨（close < lower Bollinger band）",
    "close > upper Bollinger band": "收盘价高于布林带上轨（close > upper Bollinger band）",
    "close is inside Bollinger bands": "收盘价位于布林带上下轨之间（close is inside Bollinger bands）",
    "within 2% above support": "收盘价距离支撑位不超过 2%（within 2% above support）",
    "not near support": "收盘价没有接近支撑位（not near support）",
    "within 2% below resistance": "收盘价距离阻力位不超过 2%（within 2% below resistance）",
    "not near resistance": "收盘价没有接近阻力位（not near resistance）",
}


def display_rule(rule: str) -> str:
    """Translate a deterministic rule for people while retaining its stable text."""

    return RULE_LABELS.get(rule, rule)


def display_trend_rule(rule: str) -> str:
    trend_rule_labels = {
        "latest_close > sma_5 > sma_20": "最新收盘价 > SMA5 > SMA20（latest_close > sma_5 > sma_20）",
        "latest_close < sma_5 < sma_20": "最新收盘价 < SMA5 < SMA20（latest_close < sma_5 < sma_20）",
        "moving averages are not strictly aligned": "均线没有形成严格多头或空头排列（moving averages are not strictly aligned）",
    }
    return trend_rule_labels.get(rule, rule)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B2 技术分析 Agent 演示")
    parser.add_argument("--live", action="store_true", help="显式请求真实日线")
    parser.add_argument("--symbol", default="sz000001")
    parser.add_argument("--start-date", default="20260626")
    parser.add_argument("--end-date", default="20260806")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    runtime = build_default_technical_analysis_runtime(
        project_root=PROJECT_ROOT,
        policy=FinancialDataPolicy(
            timeout_seconds=arguments.timeout,
            max_attempts=arguments.attempts,
        ),
    )
    result = runtime.run(
        TechnicalAnalysisQuery(
            symbol=arguments.symbol,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            mode="live" if arguments.live else "offline",
            limit=arguments.limit,
        )
    ).to_mapping()
    report = result["report"]
    analysis = report["analysis"]

    print("=== B2 技术分析 Agent 演示 ===")
    print(f"运行模式（mode）: {report['query']['mode']}")
    print(f"股票代码（symbol）: {analysis['symbol']}")
    print(f"K 线数量（bars）: {analysis['sample_size']}")
    print(f"行情时间（as_of）: {analysis['as_of']}")
    print(f"数据来源（source）: {', '.join(analysis['sources'])}")
    print(f"收盘价（close）: {analysis['latest_close']}")
    print(
        "移动平均线（MA）: "
        f"短期均线（SMA5）={analysis['ma']['sma_5']}, "
        f"中期均线（SMA10）={analysis['ma']['sma_10']}, "
        f"长期均线（SMA20）={analysis['ma']['sma_20']}"
    )
    print(
        "指数平滑异同移动平均线（MACD）: "
        f"快线（DIF）={analysis['macd']['dif']}, "
        f"信号线（DEA）={analysis['macd']['dea']}, "
        f"柱体（HIST）={analysis['macd']['histogram']}"
    )
    print(f"相对强弱指标（RSI14）: {analysis['rsi']['rsi_14']}")
    print(
        "随机指标（KDJ）: "
        f"K 值（K）={analysis['kdj']['k']}, D 值（D）={analysis['kdj']['d']}, "
        f"J 值（J）={analysis['kdj']['j']}"
    )
    print(
        "布林带（BOLL）: "
        f"下轨（lower）={analysis['bollinger']['lower']}, "
        f"中轨（middle）={analysis['bollinger']['middle']}, "
        f"上轨（upper）={analysis['bollinger']['upper']}"
    )
    print(
        "支撑阻力位（levels）: "
        f"支撑位（support）={analysis['levels']['support_20']}, "
        f"阻力位（resistance）={analysis['levels']['resistance_20']}"
    )
    print(
        f"趋势（trend）: {TREND_LABELS.get(analysis['trend'], analysis['trend'])} "
        f"，规则：{display_trend_rule(analysis['trend_rule'])}"
    )
    signal_label = {
        "strong_bullish": "强看多（strong_bullish）",
        "bullish": "看多（bullish）",
        "neutral": "中性（neutral）",
        "bearish": "看空（bearish）",
        "strong_bearish": "强看空（strong_bearish）",
    }.get(analysis["signal_label"], analysis["signal_label"])
    print(f"综合信号评分（signal）: {analysis['signal_score']}，{signal_label}")
    print("评分组成（score components）:")
    for component in analysis["score_components"]:
        component_name = COMPONENT_LABELS.get(component["name"], component["name"])
        print(
            f"- {component_name}: {component['points']:+d} "
            f"({display_rule(component['rule'])})"
        )
    print("循环追踪（Loop trace）:")
    for event in result["loop"]["trace"]:
        print(f"- {event['event']}")
    print("Harness 追踪（Harness trace）:")
    for event in result["loop"]["harness_trace"]:
        owner = f" [{event['agent']}]" if event["agent"] else ""
        detail = f" ({event['detail']})" if event["detail"] else ""
        print(f"- {event['event']}{owner}{detail}")
    print("说明: 所有指标和评分均由确定性代码计算，不构成投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
