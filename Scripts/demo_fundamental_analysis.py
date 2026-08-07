"""Run the B2 fundamental specialist through Data Hub, Loop, and Harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    FinancialDataPolicy,
    FundamentalAnalysisQuery,
    build_default_fundamental_analysis_runtime,
)


SCORE_LABELS = {
    "strong_positive": "强正面（strong_positive）",
    "positive": "正面（positive）",
    "neutral": "中性（neutral）",
    "negative": "负面（negative）",
    "strong_negative": "强负面（strong_negative）",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B2 基本面 Agent 演示")
    parser.add_argument("--live", action="store_true", help="显式请求真实财务数据")
    parser.add_argument("--symbol", default="sz000001")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--start-year", default="2024")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    runtime = build_default_fundamental_analysis_runtime(
        project_root=PROJECT_ROOT,
        policy=FinancialDataPolicy(
            timeout_seconds=arguments.timeout,
            max_attempts=arguments.attempts,
        ),
    )
    result = runtime.run(
        FundamentalAnalysisQuery(
            symbol=arguments.symbol,
            mode="live" if arguments.live else "offline",
            limit=arguments.limit,
            start_year=arguments.start_year,
        )
    ).to_mapping()
    report = result["report"]
    analysis = report["analysis"]
    balance = analysis["statements"]["balance_sheet"]
    income = analysis["statements"]["income_statement"]
    cash_flow = analysis["statements"]["cash_flow"]
    valuation = analysis["valuation"]
    dcf = analysis["dcf"]

    print("=== B2 基本面 Agent 演示 ===")
    print(f"运行模式（mode）: {report['query']['mode']}")
    print(f"股票代码（symbol）: {analysis['symbol']}")
    print(f"最新财务期（latest period）: {analysis['latest_financial_period']}")
    print(f"数据来源（sources）: {', '.join(analysis['sources'])}")
    print(
        "资产负债表（balance sheet）: "
        f"资产={balance['assets_cny']}, "
        f"负债={balance['liabilities_cny']}, "
        f"股东权益={balance['shareholder_equity_cny']}, "
        f"计算负债率={balance['computed_debt_to_asset_percent']}%"
    )
    print(
        "利润表（income statement）: "
        f"年度收入={income['annual_revenue_cny']}, "
        f"年度净利润={income['annual_net_profit_cny']}, "
        f"净利率={income['net_margin_percent']}%, "
        f"基本每股收益（EPS）={income['basic_eps_cny']}"
    )
    print(
        "现金流量表（cash flow）: "
        f"经营现金流={cash_flow['operating_cash_flow_cny']}, "
        f"资本开支={cash_flow['capex_cny']}, "
        f"自由现金流代理值={cash_flow['free_cash_flow_proxy_cny']}"
    )
    print(
        "财务指标（indicators）: "
        f"ROE={analysis['indicators']['roe_percent']}%, "
        f"加权 ROE={analysis['indicators']['weighted_roe_percent']}%, "
        f"ROA={analysis['indicators']['roa_percent']}%, "
        f"净利润增长={analysis['indicators']['net_profit_growth_percent']}%"
    )
    print(
        "估值（valuation）: "
        f"PE={valuation['pe_dynamic']}, PB={valuation['pb']}, PS={valuation['ps']}, "
        f"规则估值分位={valuation['valuation_percentile']}%"
    )
    print(
        "简化股东收益 DCF（discounted earnings proxy）: "
        f"当前价={dcf['current_price']}, 内在价值={dcf['intrinsic_value_per_share']}, "
        f"安全边际={dcf['margin_of_safety_percent']}%"
    )
    print(
        f"综合基本面评分（score）: {analysis['score']}，"
        f"{SCORE_LABELS.get(analysis['score_label'], analysis['score_label'])}"
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
    print("说明: DCF 和评分均由确定性代码计算，结果不构成投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
