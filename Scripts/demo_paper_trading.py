"""Run one visible C3 -> local paper matching -> persistent ledger cycle."""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    C1DecisionQuery,
    CombinedAnalysisQuery,
    FinancialDataPolicy,
    FinancialGraphQuery,
    JsonPaperTradingLedger,
    PaperTradingCycleRequest,
    PaperTradingQuote,
    PaperTradingRuntime,
    PaperTradingSessionConfig,
    RiskContext,
    build_default_financial_data_tool,
    build_default_financial_graph_runtime,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="D4 本地持续模拟交易演示")
    parser.add_argument("--live", action="store_true", help="显式使用真实行情")
    parser.add_argument("--confirm", action="store_true", help="确认本次本地模拟执行")
    parser.add_argument("--symbol", default="sz000001")
    parser.add_argument("--sector", default="玻璃行业")
    parser.add_argument("--index-symbol", default="sh000300")
    parser.add_argument("--session-id", default="d4-paper-session")
    parser.add_argument("--initial-cash", default="100000")
    parser.add_argument("--requested-position", default="15")
    parser.add_argument("--evaluation-time", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="可选持久账本路径；不指定时只做无文件临时演示",
    )
    parser.add_argument(
        "--review-only",
        action="store_true",
        help="只查看已有账本，不重新运行四 Agent 或抓取行情",
    )
    return parser


def _current_position_percent(ledger: dict, symbol: str, equity: Decimal) -> Decimal:
    position = ledger["account"]["positions"].get(symbol)
    if not position or equity <= 0:
        return Decimal("0")
    value = Decimal(str(position["shares"])) * Decimal(str(position["last_price"]))
    return (value / equity * Decimal("100")).quantize(Decimal("0.01"))


def _account_equity(ledger: dict) -> Decimal:
    account = ledger["account"]
    return Decimal(account["cash"]) + sum(
        Decimal(str(item["shares"])) * Decimal(item["last_price"])
        for item in account["positions"].values()
    )


def _run(arguments: argparse.Namespace, ledger_path: Path) -> dict:
    if arguments.evaluation_time:
        evaluated_at = datetime.fromisoformat(arguments.evaluation_time)
    elif arguments.live:
        evaluated_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    else:
        evaluated_at = datetime.fromisoformat("2026-08-07T10:00:00+08:00")
    start_date = arguments.start_date or (
        evaluated_at.date() - timedelta(days=120)
    ).strftime("%Y%m%d")
    end_date = arguments.end_date or evaluated_at.strftime("%Y%m%d")
    config = PaperTradingSessionConfig(
        session_id=arguments.session_id,
        symbols=(arguments.symbol,),
        initial_cash=Decimal(arguments.initial_cash),
        started_at=evaluated_at,
        planned_end_at=evaluated_at + timedelta(days=7),
    )
    runtime = PaperTradingRuntime(store=JsonPaperTradingLedger(ledger_path))
    ledger = runtime.start(config)
    equity = _account_equity(ledger)
    current_percent = _current_position_percent(ledger, arguments.symbol, equity)
    graph_query = FinancialGraphQuery(
        c1_query=C1DecisionQuery(
            CombinedAnalysisQuery.for_symbol(
                symbol=arguments.symbol,
                sector=arguments.sector,
                index_symbol=arguments.index_symbol,
                mode="live" if arguments.live else "offline",
                start_date=start_date,
                end_date=end_date,
            )
        ),
        risk_context=RiskContext(
            account_equity=str(equity),
            current_position_percent=str(current_percent),
            requested_position_percent=arguments.requested_position,
            sector_exposure_other_percent="5",
            current_drawdown_percent="5",
            average_daily_turnover="500000000",
            evaluation_time=evaluated_at.isoformat(),
            human_confirmed=arguments.confirm,
        ),
    )
    policy = FinancialDataPolicy(
        timeout_seconds=arguments.timeout,
        max_attempts=arguments.attempts,
    )
    graph_report = build_default_financial_graph_runtime(
        project_root=PROJECT_ROOT,
        policy=policy,
    ).run(graph_query).to_mapping()["report"]
    if arguments.live:
        quote_output = build_default_financial_data_tool(
            project_root=PROJECT_ROOT,
            policy=policy,
        ).run(
            {
                "dataset": "market.realtime",
                "params": {"symbol": arguments.symbol},
                "mode": "live",
            }
        )
        record = quote_output["records"][0]
        quote = PaperTradingQuote.from_mapping(
            {
                "symbol": record["subject"],
                "price": record["fields"]["last"],
                "source": record["source"],
                "timestamp": record["timestamp"],
                "as_of": record["as_of"],
                "mode": "live",
            }
        )
        cycle_evaluated_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    else:
        quote = PaperTradingQuote.from_financial_report(graph_report)
        cycle_evaluated_at = evaluated_at
    cycle_id = (
        f"{arguments.symbol}-{quote.as_of:%Y-%m-%d}-{cycle_evaluated_at:%H%M%S}"
    )
    result = runtime.run_cycle(
        config,
        PaperTradingCycleRequest(
            cycle_id=cycle_id,
            evaluated_at=cycle_evaluated_at,
            financial_report=graph_report,
            quote=quote,
            confirmation_actor="command_line_user",
            confirmation_note=(
                "explicit --confirm for local simulation"
                if arguments.confirm
                else "confirmation not supplied"
            ),
        ),
    ).to_mapping()
    result["ledger_path"] = str(ledger_path)
    return result


def _print_result(result: dict, *, temporary: bool) -> None:
    cycle = result["cycle"]
    review = result["review"]
    account = result["account"]
    print("=== D4 持续模拟运行：单次直观验收 ===")
    print(f"本次状态: {cycle['status']}")
    print(f"数据模式: {cycle['mode']}")
    print(f"标的: {cycle['symbol']}")
    print(
        f"行情: 收盘价={cycle['quote']['close']}，来源={cycle['quote']['source']}，"
        f"数据时点={cycle['quote']['as_of']}"
    )
    print(
        f"C3 决策: {cycle['decision']['approved_action']}，"
        f"目标仓位={cycle['decision']['target_position_percent']}%"
    )
    if cycle["simulated_order"]:
        order = cycle["simulated_order"]
        print("本地模拟成交:")
        print(
            f"- {order['side']} {order['quantity']} 股，"
            f"成交价={order['execution_price']}，佣金={order['commission']}，"
            f"滑点成本={order['slippage_cost']}"
        )
        print("- 只写入 local_simulator，没有向券商发送订单。")
    elif cycle["status"] == "pending_human_confirmation":
        print("本地模拟成交: 未执行；请显式添加 --confirm。")
    else:
        print("本地模拟成交: 本次无需调仓。")
    print(f"账户现金: {account['cash']} 元")
    print(f"持仓: {account['positions']}")
    print("运行记录汇总:")
    print(
        f"- cycles={review['cycle_count']}，fills={review['simulated_fill_count']}，"
        f"failures={review['failure_count']}，"
        f"confirmations={review['confirmation_record_count']}"
    )
    print(
        f"- 真实行情交易日={review['live_trading_day_count']}，"
        f"日历覆盖={review['live_calendar_coverage_days']} 天"
    )
    print(
        f"- 连续运行条件={'已满足' if review['duration_requirement_met'] else '尚未满足'}；"
        f"T4.3={review['formal_task_status']}"
    )
    print("执行安全边界: simulation_only=true, real_trading_allowed=false")
    if temporary:
        print("账本: 本次使用临时账本，退出后不保留文件。")
    else:
        print(f"账本: {result['ledger_path']}（后续运行会继续追加到同一文件）")


def _print_review(review: dict, ledger_path: Path) -> None:
    print("=== D4 持续模拟运行：账本状态 ===")
    print(f"session: {review['session_id']}")
    print(f"计划周期: {review['started_at']} -> {review['planned_end_at']}")
    print(
        f"运行={review['cycle_count']}，成交={review['simulated_fill_count']}，"
        f"失败={review['failure_count']}，确认={review['confirmation_record_count']}"
    )
    print(
        f"真实行情交易日={review['live_trading_day_count']}，"
        f"日历覆盖={review['live_calendar_coverage_days']} 天"
    )
    print(
        f"连续运行条件={'已满足' if review['duration_requirement_met'] else '尚未满足'}；"
        f"T4.3={review['formal_task_status']}"
    )
    if review["latest_cycle"]:
        latest = review["latest_cycle"]
        print(
            f"最近运行: {latest['evaluated_at']}，{latest['symbol']}，"
            f"status={latest['status']}，mode={latest['mode']}"
        )
    if review["latest_failure"]:
        failure = review["latest_failure"]
        print(
            f"最近失败: {failure['error_type']}，{failure['message']}，"
            f"recovered={str(failure['recovered']).lower()}"
        )
    print(f"账户: 现金={review['account']['cash']}，持仓={review['account']['positions']}")
    print("执行安全边界: simulation_only=true, real_trading_allowed=false")
    print(f"账本: {ledger_path}")


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.review_only:
        if arguments.ledger is None:
            print("--review-only 必须同时指定 --ledger", file=sys.stderr)
            return 2
        review = PaperTradingRuntime(
            store=JsonPaperTradingLedger(arguments.ledger)
        ).review()
        _print_review(review, arguments.ledger)
        return 0
    if arguments.ledger is not None:
        result = _run(arguments, arguments.ledger)
        _print_result(result, temporary=False)
        return 0
    with tempfile.TemporaryDirectory() as temp_dir:
        result = _run(arguments, Path(temp_dir) / "paper-ledger.json")
        _print_result(result, temporary=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
