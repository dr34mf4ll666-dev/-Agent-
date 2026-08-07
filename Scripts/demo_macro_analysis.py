"""Run the B2 market and macro specialist through Data Hub, Loop, and Harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    FinancialDataPolicy,
    MacroAnalysisQuery,
    build_default_macro_analysis_runtime,
)


LABELS = {
    "strong_positive": "强正面（strong_positive）",
    "positive": "正面（positive）",
    "neutral": "中性（neutral）",
    "negative": "负面（negative）",
    "strong_negative": "强负面（strong_negative）",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B2 大盘/宏观 Agent 演示")
    parser.add_argument("--live", action="store_true", help="显式请求真实大盘、资金和宏观数据")
    parser.add_argument("--index-symbol", default="sh000300")
    parser.add_argument("--symbol", default="sz000001")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--start-date", default="20240101")
    parser.add_argument("--end-date", default="20260807")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    runtime = build_default_macro_analysis_runtime(
        project_root=PROJECT_ROOT,
        policy=FinancialDataPolicy(
            timeout_seconds=arguments.timeout,
            max_attempts=arguments.attempts,
        ),
    )
    result = runtime.run(
        MacroAnalysisQuery(
            index_symbol=arguments.index_symbol,
            symbol=arguments.symbol,
            mode="live" if arguments.live else "offline",
            limit=arguments.limit,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
        )
    ).to_mapping()
    report = result["report"]
    analysis = report["analysis"]
    index = analysis["index"]
    funds = analysis["funds"]
    sentiment = analysis["sentiment"]
    macro = analysis["macro"]

    print("=== B2 大盘/宏观 Agent 演示 ===")
    print(f"运行模式（mode）: {report['query']['mode']}")
    print(f"指数（index）: {analysis['index_symbol']}")
    print(f"关联股票（symbol）: {analysis['symbol']}")
    print(f"数据来源（sources）: {', '.join(analysis['sources'])}")
    print(
        "指数趋势（index trend）: "
        f"最新收盘={index['latest_close']}, 最近单日={index['latest_return_percent']}%, "
        f"窗口收益={index['window_return_percent']}%，趋势={index['trend']}"
    )
    print(
        "资金面（funds proxy）: "
        f"净流入={funds['net_flow_cny']} 元，流向={funds['direction']}，"
        f"净流入占成交额={funds['flow_ratio_percent']}%"
    )
    print(
        "情绪（sentiment）: "
        f"研报最新评级={sentiment['latest_rating']}，"
        f"研究标签={sentiment['research_label']}，综合标签={sentiment['label']}"
    )
    print(
        "宏观环境（macro）: "
        f"GDP={macro['gdp_current_percent']}%（变化 {macro['gdp_change_percent']}%），"
        f"1W SHIBOR={macro['shibor_1w']}%（变化 {macro['shibor_1w_change']}%），"
        f"1Y LPR={macro['lpr_1y']}%"
    )
    print(
        "Market Regime: "
        f"{analysis['market_regime']['label']}，规则={analysis['market_regime']['rule']}"
    )
    print(
        "风险偏好（risk appetite）: "
        f"{analysis['risk_appetite']['label']}，评分={analysis['risk_appetite']['score']}"
    )
    print(
        f"综合评分（score）: {analysis['score']}，"
        f"{LABELS.get(analysis['score_label'], analysis['score_label'])}"
    )
    print("评分组成（score components）:")
    for component in analysis["score_components"]:
        print(f"- {component['name']}: {component['points']:+d} ({component['rule']})")
    print("循环追踪（Loop trace）:")
    for event in result["loop"]["trace"]:
        print(f"- {event['event']}")
    print("Harness 追踪（Harness trace）:")
    for event in result["loop"]["harness_trace"]:
        owner = f" [{event['agent']}]" if event["agent"] else ""
        detail = f" ({event['detail']})" if event["detail"] else ""
        print(f"- {event['event']}{owner}{detail}")
    print("说明: 资金面是关联股票代理，Market Regime 和风险偏好是项目确定性规则；结果不构成投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
