"""D1 first-slice backtest over a captured real-market fixture."""

from __future__ import annotations

import json
import sys
from datetime import datetime
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
    MarketDataSeries,
)


FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "technical_market_daily_30.json"


def _load_captured_series() -> MarketDataSeries:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    dataset = next(
        item for item in payload["datasets"] if item["dataset"] == "market.daily"
    )
    return MarketDataSeries.from_records(
        {
            "symbol": record["subject"],
            "open": record["fields"]["open"],
            "high": record["fields"]["high"],
            "low": record["fields"]["low"],
            "close": record["fields"]["close"],
            "volume": record["fields"]["volume_shares"],
            "source": record["source"],
            "timestamp": record["timestamp"],
            "as_of": record["as_of"],
        }
        for record in dataset["records"]
    )


def main() -> int:
    series = _load_captured_series()
    signals = (
        BacktestSignal(
            symbol=series.symbol,
            signal_at=datetime.fromisoformat("2026-07-02T15:00:00+08:00"),
            target_position_percent=Decimal("50"),
            source="scripted_d1_mechanism_fixture",
            rationale="D1 mechanism check: raise target position to 50%",
        ),
        BacktestSignal(
            symbol=series.symbol,
            signal_at=datetime.fromisoformat("2026-07-31T15:00:00+08:00"),
            target_position_percent=Decimal("0"),
            source="scripted_d1_mechanism_fixture",
            rationale="D1 mechanism check: reduce target position to 0%",
        ),
    )
    result = BacktestEngine().run(
        BacktestRequest(
            series=series,
            signals=signals,
            config=BacktestConfig(),
        )
    ).to_mapping()

    print("=== D1 无未来数据回测核心演示 ===")
    print(f"股票: {result['symbol']}")
    print(
        f"行情: {result['period']['start']} 至 {result['period']['end']}，"
        f"共 {result['period']['bar_count']} 根真实捕获日线"
    )
    print("数据来源: " + ", ".join(result["market_data"]["sources"]))
    print("信号说明: 两条信号是用于验证回测机制的脚本化输入，不是历史 C3 投资结论。")

    print("\n【信号时间与执行时间】")
    for order in result["orders"]:
        print(
            f"- {order['side']}: 信号={order['signal_at']}，"
            f"执行={order['execution_at']}，"
            f"下一日原始开盘价={order['raw_open']}，"
            f"含滑点成交价={order['execution_price']}，数量={order['quantity']}股"
        )
    print(
        "- 同一根K线成交="
        f"{str(result['time_semantics']['same_bar_execution_allowed']).lower()}"
    )
    print(
        "- 执行层使用未来数据="
        f"{str(result['time_semantics']['execution_layer_uses_future_data']).lower()}"
    )
    print("- 信号生成无未来数据验证=尚未完成（当前为预计算机制演示信号）")

    print("\n【交易成本】")
    costs = result["costs"]
    print(f"- 佣金: {costs['commission_cny']} 元")
    print(f"- 卖出印花税: {costs['stamp_duty_cny']} 元")
    print(f"- 滑点成本: {costs['slippage_cny']} 元")
    print(f"- 总成本: {costs['total_cny']} 元")

    print("\n【绩效结果】")
    metrics = result["metrics"]
    print(f"- 初始权益: {metrics['initial_equity']} 元")
    print(f"- 最终权益: {metrics['final_equity']} 元")
    print(f"- 总收益率: {metrics['total_return_percent']}%")
    print(f"- 最大回撤: {metrics['max_drawdown_percent']}%")
    print(f"- 年化夏普比率: {metrics['annualized_sharpe']}")
    print(f"- 已平仓交易: {metrics['closed_trade_count']}")
    print(f"- 胜率: {metrics['win_rate_percent']}%")
    print(f"- 盈亏比: {metrics['profit_loss_ratio'] or '暂无亏损交易，无法计算'}")

    print("\n【安全与当前边界】")
    print(f"- simulation_only={str(result['simulation_only']).lower()}")
    print(f"- order_created={str(result['order_created']).lower()}")
    print(f"- real_trading_allowed={str(result['real_trading_allowed']).lower()}")
    for limitation in result["limitations"]:
        print(f"- {limitation}")
    print("- 本次结果只显示在终端，不生成文件。")

    checks = {
        "下一可交易日执行": all(
            order["execution_at"] > order["signal_at"] for order in result["orders"]
        ),
        "交易成本已计入": Decimal(costs["total_cny"]) > 0,
        "绩效指标已展示": metrics["total_return_percent"] is not None,
        "真实交易保持关闭": (
            result["simulation_only"]
            and not result["order_created"]
            and not result["real_trading_allowed"]
        ),
    }
    print("\n【D1 本切片验收结论】")
    for label, passed in checks.items():
        print(f"- {'通过' if passed else '未通过'}：{label}")
    print("总体结果: " + ("通过" if all(checks.values()) else "未通过"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
