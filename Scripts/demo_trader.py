"""Run C1 and convert its result into a simulation-only Trader candidate."""

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
    TraderQuery,
    build_default_c1_decision_runtime,
    build_default_trader_runtime,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C2 Trader 模拟候选信号演示")
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
    policy = FinancialDataPolicy(
        timeout_seconds=arguments.timeout,
        max_attempts=arguments.attempts,
    )
    c1_query = C1DecisionQuery(
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
    c1_result = build_default_c1_decision_runtime(
        project_root=PROJECT_ROOT,
        policy=policy,
    ).run(c1_query).to_mapping()
    result = build_default_trader_runtime().run(
        TraderQuery(c1_result)
    ).to_mapping()
    report = result["report"]
    signal = report["signal"]
    interval = report["target_price_interval"]
    market = report["market_context"]
    execution = report["execution"]

    print("=== C2 Trader 模拟候选信号演示 ===")
    print(f"运行模式（mode）: {report['mode']}")
    print(f"分析标的（symbol）: {report['symbol']}")
    print(f"候选动作: {signal['action']}（{signal['label']}）")
    print(f"触发规则: {signal['rule']}")
    print(f"C1 加权评分: {signal['weighted_score']}")
    print(
        "目标价研究区间: "
        f"{interval['lower']} <= {interval['reference']} <= {interval['upper']}"
    )
    print(f"证据一致性置信度: {report['confidence']} / 100（不是盈利概率）")
    print(
        f"市场环境: regime={market['regime']}，"
        f"risk_appetite={market['risk_appetite']}"
    )
    print(f"C1 门控后的仓位上限: {market['position_cap_percent']}%")
    print(f"来源数量: {len(report['provenance']['sources'])}")
    print("执行安全边界:")
    print(f"- simulation_only={str(execution['simulation_only']).lower()}")
    print(f"- order_created={str(execution['order_created']).lower()}")
    print(
        "- real_trading_allowed="
        f"{str(execution['real_trading_allowed']).lower()}"
    )
    print(
        "- human_confirmation_required="
        f"{str(execution['human_confirmation_required']).lower()}"
    )
    print(f"- status={execution['status']}")
    print("Trader trace:")
    for event in result["trace"]:
        print(f"- {event['event']} ({event['detail']})")
    print("Harness trace:")
    for event in result["harness_trace"]:
        detail = f" ({event['detail']})" if event["detail"] else ""
        print(f"- {event['event']} [{event['agent']}]{detail}")
    print(
        "当前脚本边界: 本脚本只演示 Trader 候选信号；"
        "请运行 demo_c2_trading.py 验证完整 Risk Manager。系统没有创建订单。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
