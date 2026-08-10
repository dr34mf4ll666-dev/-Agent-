"""Capture the fixed D1 stock pool and benchmark through verified providers."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    AkShareTencentDailyAdapter,
    DailyBarQuery,
    MarketDataFetchPolicy,
)


TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_OUTPUT = PROJECT_ROOT / "tests" / "fixtures" / "d1_real_market_pool.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="捕获 D1 固定真实行情样本")
    parser.add_argument("--start-date", default="20250807")
    parser.add_argument("--end-date", default="20260807")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["sz000001", "sh600000", "sh601398"],
    )
    parser.add_argument("--benchmark", default="sh000300")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def _bar_mapping(bar) -> dict[str, object]:
    return {
        "symbol": bar.symbol,
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": bar.volume,
        "source": bar.source,
        "timestamp": bar.timestamp.isoformat(),
        "as_of": bar.as_of.isoformat(),
    }


def _capture_benchmark(symbol: str, start: str, end: str) -> list[dict[str, object]]:
    import akshare as ak

    frame = ak.stock_zh_index_daily(symbol=symbol)
    dates = frame["date"].astype(str).str.replace("-", "", regex=False)
    frame = frame[(dates >= start) & (dates <= end)]
    fetched_at = datetime.now(TZ)
    return [
        {
            "symbol": symbol,
            "open": str(row["open"]),
            "high": str(row["high"]),
            "low": str(row["low"]),
            "close": str(row["close"]),
            "volume": int(row["volume"]),
            "source": "akshare.stock_zh_index_daily",
            "timestamp": fetched_at.isoformat(),
            "as_of": datetime.fromisoformat(str(row["date"]))
            .replace(hour=15, tzinfo=TZ)
            .isoformat(),
        }
        for row in frame.to_dict(orient="records")
    ]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    start = datetime.strptime(args.start_date, "%Y%m%d").date()
    end = datetime.strptime(args.end_date, "%Y%m%d").date()
    provider = AkShareTencentDailyAdapter()
    policy = MarketDataFetchPolicy(timeout_seconds=15, max_attempts=2)
    series = {}
    for symbol in args.symbols:
        result = provider.fetch(
            DailyBarQuery(symbol=symbol, start_date=start, end_date=end),
            policy,
        )
        series[symbol] = [_bar_mapping(bar) for bar in result.series.bars]
        print(f"已捕获 {symbol}: {len(result.series.bars)} 根")
    benchmark = _capture_benchmark(
        args.benchmark,
        args.start_date,
        args.end_date,
    )
    payload = {
        "version": 1,
        "dataset_type": "captured_real_sample",
        "description": "D1 fixed real A-share pool and CSI 300 benchmark",
        "captured_at": datetime.now(TZ).isoformat(),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "symbols": args.symbols,
        "benchmark_symbol": args.benchmark,
        "series": series,
        "benchmark": benchmark,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"基准 {args.benchmark}: {len(benchmark)} 根")
    print(f"已写入: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
