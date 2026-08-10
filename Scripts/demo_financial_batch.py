"""Run the C3 financial Graph for a real multi-stock acceptance batch."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    C1DecisionQuery,
    CombinedAnalysisQuery,
    FinancialBatchQuery,
    FinancialBatchRuntime,
    FinancialDataPolicy,
    FinancialGraphQuery,
    RiskContext,
    build_default_financial_graph_runtime,
)


DEFAULT_BANK_SYMBOLS = (
    "sz000001", "sh600000", "sh600015", "sh600016", "sh600036",
    "sh601009", "sh601166", "sh601169", "sh601229", "sh601288",
    "sh601328", "sh601398", "sh601658", "sh601818", "sh601838",
    "sh601939", "sh601988", "sh601998", "sz002142", "sz002807",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C3 二十只股票真实批量验收")
    parser.add_argument("--live", action="store_true", help="必须显式启用真实数据")
    parser.add_argument("--confirm", action="store_true", help="显式模拟人工确认")
    parser.add_argument("--symbols", help="可选：英文逗号分隔的唯一股票代码")
    parser.add_argument(
        "--sector",
        default="金融行业",
        help="真实行业快照采用数据源板块名；银行股默认映射到金融行业",
    )
    parser.add_argument("--index-symbol", default="sh000300")
    parser.add_argument("--start-date", default="20240101")
    parser.add_argument("--end-date", default="20260807")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, choices=(1, 2, 3), default=1)
    return parser


def _query(symbol: str, arguments: argparse.Namespace) -> FinancialGraphQuery:
    return FinancialGraphQuery(
        c1_query=C1DecisionQuery(
            combined_query=CombinedAnalysisQuery.for_symbol(
                symbol=symbol,
                sector=arguments.sector,
                index_symbol=arguments.index_symbol,
                mode="live",
                start_date=arguments.start_date,
                end_date=arguments.end_date,
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
            evaluation_time="2026-08-07T10:00:00+08:00",
            human_confirmed=arguments.confirm,
        ),
    )


def _progress(index: int, total: int, symbol: str, status: str) -> None:
    labels = {"started": "开始", "completed": "完成", "failed": "失败"}
    print(f"[{index}/{total}] {symbol}: {labels[status]}", flush=True)


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not arguments.live:
        print("批量验收拒绝启动：必须显式添加 --live，不能用一份离线样本冒充 20 只股票。")
        return 2
    symbols = (
        tuple(item.strip() for item in arguments.symbols.split(",") if item.strip())
        if arguments.symbols
        else DEFAULT_BANK_SYMBOLS
    )
    policy = FinancialDataPolicy(
        timeout_seconds=arguments.timeout,
        max_attempts=arguments.attempts,
    )
    runtime = FinancialBatchRuntime(
        lambda: build_default_financial_graph_runtime(
            project_root=PROJECT_ROOT,
            policy=policy,
        ),
        progress_callback=_progress,
    )
    result = runtime.run(
        FinancialBatchQuery([_query(symbol, arguments) for symbol in symbols])
    ).to_mapping()

    print("\n=== C3 多股票完整金融 Graph 验收 ===")
    print(
        f"请求={result['requested_count']}，完成={result['completed_count']}，"
        f"失败={result['failed_count']}"
    )
    print(f"20只股票验收通过={str(result['acceptance_20_met']).lower()}")
    print("\n交易建议清单（均为研究与模拟建议）:")
    for item in result["trade_advice"]:
        interval = item["target_price_interval"]
        print(
            f"- {item['symbol']}: 研究={item['research_inclination']}，"
            f"置信度={item['confidence']}/100，"
            f"区间={interval['lower']}–{interval['upper']}，"
            f"候选={item['candidate_action']}，最终={item['approved_action']}，"
            f"仓位={item['approved_position_percent']}%，"
            f"预计单笔亏损={item['estimated_single_trade_loss_percent']}%"
        )
    print("\nGraph/Harness 审计摘要:")
    for audit in result["audit_logs"]:
        statuses = audit["graph"]["statuses"]
        completed = sum(status == "completed" for status in statuses.values())
        skipped = sum(status == "skipped" for status in statuses.values())
        specialist_passes = sum(
            any(event["event"] == "postflight.passed" for event in trace)
            for trace in audit["specialist_harness_trace"].values()
        )
        print(
            f"- {audit['symbol']}: Graph完成节点={completed}，跳过节点={skipped}，"
            f"Specialist Harness通过={specialist_passes}/4，"
            f"来源数={len(audit['sources'])}"
        )
    if result["failures"]:
        print("\n失败明细:")
        for failure in result["failures"]:
            print(
                f"- {failure['symbol']}: {failure['error_type']} - "
                f"{failure['message']}"
            )
    print("\n内存交付结果:")
    print(f"- 标准化投研报告: {len(result['reports'])} 份")
    print(f"- 交易建议: {len(result['trade_advice'])} 条")
    print(f"- Graph/Harness 审计日志: {len(result['audit_logs'])} 份")
    print("- 本次不生成报告文件；结果显示在终端并保留在运行返回值中。")
    print("- simulation_only=true, order_created=false, real_trading_allowed=false")
    return 0 if result["acceptance_20_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
