"""Run complete C2: C1 research, Trader candidate, and Risk Manager review."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    C1DecisionQuery,
    C2TradingQuery,
    CombinedAnalysisQuery,
    FinancialDataPolicy,
    RiskContext,
    build_default_c1_decision_runtime,
    build_default_c2_trading_runtime,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C2 Trader 与 Risk Manager 完整演示")
    parser.add_argument("--live", action="store_true", help="显式请求真实数据")
    parser.add_argument("--confirm", action="store_true", help="显式模拟人工确认")
    parser.add_argument("--symbol", default="sz000001")
    parser.add_argument("--sector", default="玻璃行业")
    parser.add_argument("--index-symbol", default="sh000300")
    parser.add_argument("--start-date", default="20240101")
    parser.add_argument("--end-date", default="20260807")
    parser.add_argument("--rounds", type=int, choices=(2, 3), default=2)
    parser.add_argument("--base-position-cap", type=int, default=30)
    parser.add_argument("--account-equity", default="100000")
    parser.add_argument("--current-position", default="0")
    parser.add_argument("--requested-position", default="15")
    parser.add_argument("--sector-exposure-other", default="5")
    parser.add_argument("--drawdown", default="5")
    parser.add_argument("--average-daily-turnover", default="500000000")
    parser.add_argument(
        "--evaluation-time",
        default="2026-08-07T10:00:00+08:00",
        help="带 +08:00 时区的评估时间",
    )
    parser.add_argument("--stop-loss")
    parser.add_argument("--take-profit")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
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
        policy=FinancialDataPolicy(
            timeout_seconds=arguments.timeout,
            max_attempts=arguments.attempts,
        ),
    ).run(c1_query).to_mapping()
    interval = c1_result["report"]["synthesis"]["target_price_interval"]
    risk_context = RiskContext(
        account_equity=arguments.account_equity,
        current_position_percent=arguments.current_position,
        requested_position_percent=arguments.requested_position,
        sector_exposure_other_percent=arguments.sector_exposure_other,
        current_drawdown_percent=arguments.drawdown,
        average_daily_turnover=arguments.average_daily_turnover,
        evaluation_time=arguments.evaluation_time,
        stop_loss_price=arguments.stop_loss or interval["lower"],
        take_profit_price=arguments.take_profit or interval["upper"],
        human_confirmed=arguments.confirm,
    )
    result = build_default_c2_trading_runtime().run(
        C2TradingQuery(c1_result, risk_context)
    ).to_mapping()
    report = result["report"]
    trader = report["trader"]["report"]
    risk_result = report["risk_manager"]
    risk = risk_result["report"]
    decision = risk["risk_decision"]
    position = risk["position"]
    execution = risk["execution"]

    print("=== C2 Trader + Risk Manager 完整演示 ===")
    print(f"运行模式（mode）: {report['mode']}")
    print(f"分析标的（symbol）: {report['symbol']}")
    print(
        f"Trader 候选: {trader['signal']['action']}"
        f"（{trader['signal']['label']}）"
    )
    print(
        "目标价研究区间: "
        f"{interval['lower']} <= {interval['reference']} <= {interval['upper']}"
    )
    print(f"置信度: {trader['confidence']} / 100（不是盈利概率）")
    print("模拟账户与风控场景输入:")
    print(f"- account_equity={risk_context.account_equity}")
    print(f"- evaluation_time={risk_context.evaluation_time}")
    print(f"- current_drawdown_percent={risk_context.current_drawdown_percent}")
    print(f"- average_daily_turnover={risk_context.average_daily_turnover}")
    print("Risk Manager 结论:")
    print(f"- status={decision['status']}")
    print(f"- requested_action={decision['requested_action']}")
    print(f"- approved_action={decision['approved_action']}")
    print(f"- reason={decision['reason']}")
    print("仓位计算:")
    print(f"- 当前仓位: {position['current_percent']}%")
    print(f"- 请求仓位: {position['requested_percent']}%")
    print(f"- 批准仓位: {position['approved_percent']}%")
    print(f"- 单笔风险上限对应仓位: {position['single_trade_risk_cap_percent']}%")
    print(f"- 行业上限对应仓位: {position['sector_cap_percent']}%")
    print(f"- 最终行业暴露: {position['final_sector_exposure_percent']}%")
    print(f"- 预计单笔亏损: {position['estimated_single_trade_loss_percent']}%")
    print("风险检查:")
    for check in risk["risk_checks"]:
        print(f"- {check['name']}: {check['status']} ({check['detail']})")
    print("执行安全边界:")
    print(
        "- simulation_execution_allowed="
        f"{str(execution['simulation_execution_allowed']).lower()}"
    )
    print(
        "- human_confirmation_required="
        f"{str(execution['human_confirmation_required']).lower()}"
    )
    print(f"- human_confirmed={str(execution['human_confirmed']).lower()}")
    print(f"- order_created={str(execution['order_created']).lower()}")
    print(
        "- real_trading_allowed="
        f"{str(execution['real_trading_allowed']).lower()}"
    )
    print("C2 trace:")
    for event in result["trace"]:
        print(f"- {event['event']} ({event['detail']})")
    print("Risk Manager Harness trace:")
    for event in risk_result["harness_trace"]:
        detail = f" ({event['detail']})" if event["detail"] else ""
        print(f"- {event['event']} [{event['agent']}]{detail}")
    print(
        "当前阶段结论: C2 已完成 Trader 与 Risk Manager；"
        "结果只允许后续模拟执行，未创建任何订单。"
    )
    if decision["status"] == "pending_human_confirmation":
        print("提示: 添加 --confirm 可模拟完成本次人工确认。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
