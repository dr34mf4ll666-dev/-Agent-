"""使用离线模拟行情运行 TechnicalAnalysisAgent。"""

from __future__ import annotations

import csv
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "synthetic_market_bars_30.csv"
)
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import AgentHarness, AgentRequest  # noqa: E402
from agent_platform.finance import (  # noqa: E402
    MarketDataSeries,
    TechnicalAnalysisAgent,
)


def main() -> int:
    with FIXTURE_PATH.open(encoding="utf-8", newline="") as fixture_file:
        series = MarketDataSeries.from_records(csv.DictReader(fixture_file))

    harness_result = AgentHarness(TechnicalAnalysisAgent()).run(
        AgentRequest(
            task="analyze the latest technical trend",
            context={"market_data": series},
        )
    )
    analysis = harness_result.response.metadata["analysis"]

    print("=== 离线技术分析演示 ===")
    print(harness_result.response.content)
    print("\n结构化指标:")
    print(f"证券: {analysis['symbol']}")
    print(f"数据时间: {analysis['as_of']}")
    print(f"样本数量: {analysis['sample_size']}")
    print(f"最新收盘价: {analysis['latest_close']}")
    print(f"单日收益率: {analysis['daily_return']}")
    print(f"SMA5: {analysis['sma_5']}")
    print(f"SMA20: {analysis['sma_20']}")
    print(f"趋势: {analysis['trend']}")
    print(f"规则: {analysis['trend_rule']}")
    print(f"数据来源: {', '.join(analysis['sources'])}")
    print("\nHarness trace:")
    for event in harness_result.trace:
        print(f"- {event.event}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
