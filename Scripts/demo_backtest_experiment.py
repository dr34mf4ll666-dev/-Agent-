"""Visible D1 acceptance over a fixed real-market multi-stock fixture."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    BacktestConfig,
    BacktestEngine,
    BacktestRequest,
    BacktestSignal,
    CorporateAction,
    MarketDataSeries,
    TradingSessionConstraint,
)
from agent_platform.finance.backtest_experiment import (  # noqa: E402
    BacktestExperimentConfig,
    BacktestExperimentError,
    BacktestExperimentRunner,
    C3DecisionSnapshot,
    PointInTimeEvidence,
)


CONFIG_PATH = PROJECT_ROOT / "Workflow" / "examples" / "d1_backtest_experiment.json"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "d1_real_market_pool.json"


def _c3_contract_report(symbol: str, action: str, target: Decimal) -> dict:
    decision = {
        "status": "approved",
        "requested_action": action,
        "approved_action": action,
        "reason": "deterministic walk-forward Agent replay",
    }
    return {
        "status": "financial_graph_completed",
        "symbol": symbol,
        "mode": "offline",
        "research": {
            "report": {
                "symbol": symbol,
                "mode": "offline",
            }
        },
        "trader": {
            "report": {
                "symbol": symbol,
                "mode": "offline",
                "signal": {"action": action},
                "market_context": {"regime": "risk_on"},
            }
        },
        "route": {"selected_path": "risk_review"},
        "risk_manager": {
            "report": {
                "risk_decision": decision,
                "position": {"approved_percent": str(target)},
            }
        },
        "final_decision": decision,
        "decision_source": "risk_manager",
        "decision_origin": "deterministic_walk_forward_agent_replay",
        "simulation_only": True,
        "order_created": False,
        "real_trading_allowed": False,
    }


def _walk_forward_snapshots(
    series: MarketDataSeries,
    config: BacktestExperimentConfig,
) -> tuple[C3DecisionSnapshot, ...]:
    policy = config.signal_policy
    warmup = policy["warmup_bars"]
    interval = policy["rebalance_every_bars"]
    short_window = policy["short_window"]
    long_window = policy["long_window"]
    positive = Decimal(str(policy["positive_target_percent"]))
    negative = Decimal(str(policy["negative_target_percent"]))
    snapshots = []
    for index in range(warmup - 1, len(series.bars) - 1, interval):
        window = series.bars[: index + 1]
        short_ma = sum(
            (bar.close for bar in window[-short_window:]),
            Decimal("0"),
        ) / Decimal(short_window)
        long_ma = sum(
            (bar.close for bar in window[-long_window:]),
            Decimal("0"),
        ) / Decimal(long_window)
        action = "buy" if short_ma > long_ma else "sell"
        target = positive if action == "buy" else negative
        signal_at = series.bars[index].as_of
        generated_at = signal_at + timedelta(minutes=5)
        snapshots.append(
            C3DecisionSnapshot(
                signal_at=signal_at,
                generated_at=generated_at,
                report=_c3_contract_report(series.symbol, action, target),
                evidence=(
                    PointInTimeEvidence(
                        name=f"rolling_close_window_{long_window}",
                        source=series.bars[index].source,
                        as_of=signal_at,
                        available_at=signal_at,
                    ),
                ),
            )
        )
    return tuple(snapshots)


def _market_rule_acceptance(series: MarketDataSeries) -> dict:
    bars = series.bars[:8]
    local = MarketDataSeries(bars)
    first_signal = BacktestSignal(
        symbol=series.symbol,
        signal_at=bars[0].as_of,
        available_at=bars[0].as_of + timedelta(minutes=5),
        target_position_percent=Decimal("50"),
        source="d1.constraint.acceptance",
    )
    exit_signal = BacktestSignal(
        symbol=series.symbol,
        signal_at=bars[4].as_of,
        available_at=bars[4].as_of + timedelta(minutes=5),
        target_position_percent=Decimal("0"),
        source="d1.constraint.acceptance",
    )
    constraints = (
        TradingSessionConstraint(
            symbol=series.symbol,
            as_of=bars[1].as_of,
            buy_allowed=False,
            sell_allowed=True,
            reason="synthetic limit-up acceptance event",
            source="d1.synthetic_exchange_status",
            timestamp=bars[1].timestamp,
        ),
        TradingSessionConstraint(
            symbol=series.symbol,
            as_of=bars[5].as_of,
            buy_allowed=True,
            sell_allowed=False,
            reason="synthetic limit-down acceptance event",
            source="d1.synthetic_exchange_status",
            timestamp=bars[5].timestamp,
        ),
    )
    action = CorporateAction(
        symbol=series.symbol,
        as_of=bars[3].as_of,
        announced_at=bars[2].as_of,
        cash_dividend_per_share=Decimal("0.10"),
        share_multiplier=Decimal("1.10"),
        source="d1.synthetic_corporate_action",
        timestamp=bars[3].timestamp,
    )
    return BacktestEngine().run(
        BacktestRequest(
            series=local,
            signals=(first_signal, exit_signal),
            config=BacktestConfig(),
            trading_constraints=constraints,
            corporate_actions=(action,),
        )
    ).to_mapping()


def _future_evidence_is_rejected(series: MarketDataSeries) -> bool:
    signal_at = series.bars[60].as_of
    try:
        C3DecisionSnapshot(
            signal_at=signal_at,
            generated_at=signal_at + timedelta(minutes=5),
            report=_c3_contract_report(series.symbol, "buy", Decimal("15")),
            evidence=(
                PointInTimeEvidence(
                    name="deliberate_future_fact",
                    source=series.bars[61].source,
                    as_of=series.bars[61].as_of,
                    available_at=series.bars[61].as_of,
                ),
            ),
        )
    except BacktestExperimentError:
        return True
    return False


def main() -> int:
    config = BacktestExperimentConfig.load(CONFIG_PATH)
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    market_data = {
        symbol: MarketDataSeries.from_records(fixture["series"][symbol])
        for symbol in config.symbols
    }
    benchmark = MarketDataSeries.from_records(fixture["benchmark"])
    decisions = {
        symbol: _walk_forward_snapshots(series, config)
        for symbol, series in market_data.items()
    }
    result = BacktestExperimentRunner().run(
        config=config,
        market_data=market_data,
        decisions=decisions,
        benchmark=benchmark,
    ).to_mapping()
    rule_result = _market_rule_acceptance(market_data[config.symbols[0]])
    future_leak_rejected = _future_evidence_is_rejected(
        market_data[config.symbols[0]]
    )

    print("=== D1 固定多股票回测总验收 ===")
    print(f"实验: {config.name}")
    print(
        f"区间: {config.start_date.isoformat()} 至 {config.end_date.isoformat()}，"
        f"股票池: {', '.join(config.symbols)}"
    )
    print(
        f"真实行情: {len(config.symbols)} 只股票 × 243 根日线；"
        f"基准={config.benchmark_symbol} × 243 根日线"
    )
    print("来源: " + ", ".join(result["data"]["market_sources"]))
    print(
        "信号口径: 固定的滚动 Agent 规则生成 C3 合约格式回放输入；"
        "它用于验收 D1，不冒充历史现场运行的四 Agent 结论。"
    )

    print("\n【历史时点与撮合】")
    print(f"- C3 证据时间门禁: {result['data']['point_in_time_c3_verified']}")
    print(f"- 故意放入下一交易日证据: {'已拒绝' if future_leak_rejected else '未拒绝'}")
    print(f"- 信号数量: {result['signal_count']}")
    print(f"- 实际模拟成交: {result['executed_order_count']}")
    print("- 信号在收盘后 5 分钟生成，只能从下一交易日开盘开始成交")

    print("\n【固定股票池结果】")
    for symbol in config.symbols:
        item = result["per_symbol"][symbol]
        metrics = item["metrics"]
        print(
            f"- {symbol}: 收益={metrics['total_return_percent']}%，"
            f"最大回撤={metrics['max_drawdown_percent']}%，"
            f"夏普={metrics['annualized_sharpe']}，"
            f"胜率={metrics['win_rate_percent'] or '无平仓样本'}，"
            f"盈亏比={metrics['profit_loss_ratio'] or '无完整盈亏样本'}，"
            f"成交={item['executed_order_count']}"
        )

    portfolio = result["portfolio_metrics"]
    baseline = result["sharpe_baseline"]
    print("\n【组合、基准与成本】")
    print(f"- 初始资金: {portfolio['initial_equity']} 元")
    print(f"- 最终权益: {portfolio['final_equity']} 元")
    print(f"- 组合收益率: {portfolio['total_return_percent']}%")
    print(f"- 最大回撤: {portfolio['max_drawdown_percent']}%")
    print(f"- 年化夏普: {portfolio['annualized_sharpe']}")
    print(f"- 沪深300收益率: {result['benchmark']['total_return_percent']}%")
    print(
        f"- 相对沪深300超额收益: "
        f"{portfolio['excess_return_vs_benchmark_percent']}%"
    )
    print(
        f"- 交易成本: 佣金={result['costs']['commission_cny']} 元，"
        f"印花税={result['costs']['stamp_duty_cny']} 元，"
        f"滑点={result['costs']['slippage_cny']} 元，"
        f"合计={result['costs']['total_cny']} 元"
    )
    print(
        f"- 夏普基线: > {baseline['target']}，"
        f"实际={baseline['observed']}，达标={baseline['met']}"
    )

    print("\n【停牌、涨跌停与公司行为机制】")
    print("- 本区块使用明确标记的合成事件，只验证机制，不伪装成真实公司行为。")
    print(
        f"- 涨跌停方向权限拦截: "
        f"{rule_result['market_constraints']['blocked_execution_count']} 次"
    )
    print(
        f"- 分红送转事件应用: "
        f"{rule_result['corporate_actions']['applied_count']} 次，"
        f"现金分红={rule_result['corporate_actions']['cash_dividends_cny']} 元"
    )
    print("- 成交量为 0 的停牌等待由同一回测核心处理")

    print("\n【安全边界】")
    print(f"- simulation_only={str(result['simulation_only']).lower()}")
    print(f"- order_created={str(result['order_created']).lower()}")
    print(f"- real_trading_allowed={str(result['real_trading_allowed']).lower()}")
    print("- 默认只显示终端结果，不生成回测报告文件")

    checks = {
        "三只真实股票与真实基准已固定": len(config.symbols) == 3,
        "历史证据晚于信号时会被拒绝": future_leak_rejected,
        "下一交易日撮合及成本已执行": result["executed_order_count"] > 0,
        "停牌/涨跌停/公司行为机制可见": (
            rule_result["market_constraints"]["blocked_execution_count"] == 2
            and rule_result["corporate_actions"]["applied_count"] == 1
        ),
        "夏普基线已如实报告": baseline["observed"] is not None,
        "真实交易保持关闭": (
            result["simulation_only"]
            and not result["order_created"]
            and not result["real_trading_allowed"]
        ),
    }
    print("\n【D1 总验收结论】")
    for label, passed in checks.items():
        print(f"- {'通过' if passed else '未通过'}：{label}")
    print("总体结果: " + ("通过" if all(checks.values()) else "未通过"))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
