"""Run the first B1 daily-market-data Tool offline or explicitly live."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "tencent_daily_bars_000001.json"
)
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import ToolRegistry  # noqa: E402
from agent_platform.finance import (  # noqa: E402
    AkShareTencentDailyAdapter,
    DailyMarketDataTool,
    JsonDailyMarketDataAdapter,
    MarketDataFetchPolicy,
    MarketDataProviderError,
    MarketDataRequestError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B1 腾讯 A 股日线 Tool 演示")
    parser.add_argument("--live", action="store_true", help="显式调用腾讯真实接口")
    parser.add_argument("--symbol", default="sz000001", help="带市场前缀的代码")
    parser.add_argument("--start-date", default="20240102", help="YYYYMMDD")
    parser.add_argument("--end-date", default="20240105", help="YYYYMMDD")
    parser.add_argument("--timeout", type=float, default=8.0, help="单次上游请求超时")
    parser.add_argument("--attempts", type=int, default=2, help="最多尝试次数")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    provider = (
        AkShareTencentDailyAdapter()
        if arguments.live
        else JsonDailyMarketDataAdapter(FIXTURE_PATH)
    )
    tool = DailyMarketDataTool(
        provider,
        policy=MarketDataFetchPolicy(
            timeout_seconds=arguments.timeout,
            max_attempts=arguments.attempts,
        ),
    )
    registry = ToolRegistry([tool])
    try:
        output = registry.execute(
            "finance_daily_bars",
            {
                "symbol": arguments.symbol,
                "start_date": arguments.start_date,
                "end_date": arguments.end_date,
            },
        )
    except (MarketDataRequestError, MarketDataProviderError) as error:
        print("=== B1 腾讯日线 Tool 失败 ===")
        print(f"mode: {'live' if arguments.live else 'offline'}")
        if isinstance(error, MarketDataProviderError):
            print(f"code: {error.code.value}")
            print(f"source: {error.source}")
            print(f"attempts: {error.attempts}")
            if error.cause is not None:
                print(f"cause: {type(error.cause).__name__}")
        print(f"message: {error}")
        return 1

    first_bar = output["bars"][0]
    last_bar = output["bars"][-1]
    print("=== B1 腾讯 A 股日线 Tool 演示 ===")
    print(f"mode: {'live' if arguments.live else 'offline'}")
    print(f"source: {output['source']}")
    print(f"symbol: {output['symbol']}")
    print(f"bar_count: {output['bar_count']}")
    print(f"timestamp: {output['timestamp']}")
    print(
        "first_bar: "
        f"as_of={first_bar['as_of']}, close={first_bar['close']}, "
        f"volume={first_bar['volume']}"
    )
    print(
        "last_bar: "
        f"as_of={last_bar['as_of']}, close={last_bar['close']}, "
        f"volume={last_bar['volume']}"
    )
    print("trace:")
    for event in output["trace"]:
        detail = f" ({event['detail']})" if event["detail"] else ""
        print(f"- {event['event']} [attempt={event['attempt']}]{detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
