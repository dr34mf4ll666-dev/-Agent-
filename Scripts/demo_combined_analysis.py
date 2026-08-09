"""Run the complete C1 analysis, debate, synthesis, and regime gate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    C1DecisionQuery,
    CombinedAnalysisQuery,
    FinancialDataPolicy,
    build_default_c1_decision_runtime,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C1 四 Agent 并行联合分析演示")
    parser.add_argument("--live", action="store_true", help="显式请求真实数据")
    parser.add_argument("--symbol", default="sz000001")
    parser.add_argument("--sector", default="玻璃行业")
    parser.add_argument("--index-symbol", default="sh000300")
    parser.add_argument("--start-date", default="20240101")
    parser.add_argument("--end-date", default="20260807")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--rounds", type=int, choices=(2, 3), default=2)
    parser.add_argument("--base-position-cap", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    runtime = build_default_c1_decision_runtime(
        project_root=PROJECT_ROOT,
        policy=FinancialDataPolicy(
            timeout_seconds=arguments.timeout,
            max_attempts=arguments.attempts,
        ),
    )
    query = C1DecisionQuery(
        combined_query=CombinedAnalysisQuery.for_symbol(
            symbol=arguments.symbol,
            sector=arguments.sector,
            index_symbol=arguments.index_symbol,
            mode="live" if arguments.live else "offline",
            start_date=arguments.start_date,
            end_date=arguments.end_date,
        ),
        debate_rounds=arguments.rounds,
        base_position_cap_percent=arguments.base_position_cap,
    )
    result = runtime.run(query).to_mapping()
    report = result["report"]
    combined_report = report["combined_analysis"]
    summary = combined_report["summary"]
    debate_report = report["debate"]
    synthesis = report["synthesis"]
    gate = report["market_regime_gate"]

    print("=== C1 四 Agent 并行联合分析演示 ===")
    print(f"运行模式（mode）: {combined_report['mode']}")
    print(f"分析标的（symbol）: {combined_report['symbol']}")
    print("Planner: technical、fundamental、industry、macro 四个 Agent 并行运行")
    print("四路结果（specialist summary）:")
    print(
        f"- 技术分析（technical）: {summary['technical']['signal_label']}，"
        f"评分={summary['technical']['signal_score']}"
    )
    print(
        f"- 基本面（fundamental）: {summary['fundamental']['score_label']}，"
        f"评分={summary['fundamental']['score']}"
    )
    print(
        f"- 行业（industry）: {summary['industry']['score_label']}，"
        f"评分={summary['industry']['score']}，景气度={summary['industry']['prosperity']}"
    )
    print(
        f"- 大盘/宏观（macro）: Regime={summary['macro']['market_regime']}，"
        f"风险偏好={summary['macro']['risk_appetite']}，评分={summary['macro']['score']}"
    )
    print(f"汇总来源数量（source count）: {len(combined_report['sources'])}")
    print("Graph 状态（graph statuses）:")
    for node, status in result["specialist_graph"]["statuses"].items():
        print(f"- {node}: {status}")
    print("Graph 执行顺序（execution order）:")
    print(" -> ".join(result["specialist_graph"]["execution_order"]))
    print("并行波次（parallel wave）:")
    for event in result["specialist_graph"]["trace"]:
        if event["event"] == "graph.wave.started":
            print(f"- {event['detail']}")
    print(
        f"结构化辩论（Claim → Evidence → Reasoning，共 {len(debate_report['rounds'])} 轮）:"
    )
    for debate_round in debate_report["rounds"]:
        print(f"- 第 {debate_round['round']} 轮 Bull: {debate_round['bull']['claim']}")
        print(
            "  Bull 证据: "
            + ", ".join(
                f"{reference['specialist']}:{reference['path']}"
                for reference in debate_round["bull"]["evidence"]
            )
        )
        print(f"  Bull Reasoning: {debate_round['bull']['reasoning']}")
        print(f"- 第 {debate_round['round']} 轮 Bear: {debate_round['bear']['claim']}")
        print(
            "  Bear 证据: "
            + ", ".join(
                f"{reference['specialist']}:{reference['path']}"
                for reference in debate_round["bear"]["evidence"]
            )
        )
        print(f"  Bear Reasoning: {debate_round['bear']['reasoning']}")
    print(
        "综合结论（Synthesis）:"
    )
    interval = synthesis["target_price_interval"]
    print(
        f"- 综合倾向: {synthesis['inclination']} "
        f"（门控前={synthesis['raw_inclination']}）"
    )
    print(f"- 四 Agent 加权评分: {synthesis['weighted_score']}")
    print(
        f"- 目标价研究区间: {interval['lower']} <= "
        f"{interval['reference']} <= {interval['upper']}"
    )
    print(
        f"- Bull 目标价上限: {synthesis['side_targets']['bull_target_price_upper']}"
    )
    print(
        f"- Bear 目标价下限: {synthesis['side_targets']['bear_target_price_lower']}"
    )
    print(
        f"- 置信度: {synthesis['confidence']} / 100 "
        "（表示证据一致性，不是盈利概率）"
    )
    print("Consistency Check / Bias Detector:")
    print(
        f"- Consistency Check: "
        f"{report['quality']['consistency_check']['status']}"
    )
    print(f"- Bias Detector: {report['quality']['bias_detector']['status']}")
    print("Market Regime 门控:")
    print(
        f"- regime={gate['regime']}，risk_appetite={gate['risk_appetite']}，"
        f"仓位上限 {gate['base_position_cap_percent']}% "
        f"-> {gate['effective_position_cap_percent']}%"
    )
    print(f"- real_trading_allowed={str(gate['real_trading_allowed']).lower()}")
    print("C1 trace:")
    for event in result["trace"]:
        print(f"- {event['event']} ({event['detail']})")
    print(
        "当前阶段结论（stage boundary）: C1 已完成；"
        "当前结果是研究结论，不是下单指令。下一阶段是 C2 Trader 与 Risk Manager。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
