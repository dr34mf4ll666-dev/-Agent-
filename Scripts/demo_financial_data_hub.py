"""Inspect every B1 dataset offline or one dataset explicitly live."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    SUPPORTED_FINANCIAL_DATASETS,
    FinancialDataError,
    FinancialDataPolicy,
    build_default_financial_data_tool,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B1 金融 Data Hub 演示")
    parser.add_argument("--live", action="store_true", help="显式调用真实数据源")
    parser.add_argument("--dataset", help="只运行一个 dataset")
    parser.add_argument("--params-json", default="{}", help="dataset 参数 JSON")
    parser.add_argument("--symbol")
    parser.add_argument("--ts-code")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--period")
    parser.add_argument("--start-year")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--attempts", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        params = json.loads(arguments.params_json)
    except json.JSONDecodeError as error:
        print(f"params JSON 无效: {error}")
        return 2
    if not isinstance(params, dict):
        print("params JSON 必须是对象")
        return 2
    for key, value in {
        "symbol": arguments.symbol,
        "ts_code": arguments.ts_code,
        "start_date": arguments.start_date,
        "end_date": arguments.end_date,
        "period": arguments.period,
        "start_year": arguments.start_year,
        "limit": arguments.limit,
    }.items():
        if value is not None:
            params[key] = value
    if arguments.live and not arguments.dataset:
        print("live 模式必须通过 --dataset 限定一次只读请求")
        return 2
    datasets = (arguments.dataset,) if arguments.dataset else SUPPORTED_FINANCIAL_DATASETS
    tool = build_default_financial_data_tool(
        project_root=PROJECT_ROOT,
        policy=FinancialDataPolicy(
            timeout_seconds=arguments.timeout,
            max_attempts=arguments.attempts,
        ),
    )
    print("=== B1 金融 Data Hub 演示 ===")
    print(f"mode: {'live' if arguments.live else 'offline'}")
    for dataset in datasets:
        try:
            output = tool.run(
                {
                    "dataset": dataset,
                    "params": params,
                    "mode": "live" if arguments.live else "offline",
                }
            )
        except FinancialDataError as error:
            print(
                f"- {dataset}: failed code={error.code.value} "
                f"source={error.source or '-'} attempts={error.attempts}"
            )
            return 1
        first = output["records"][0]
        print(
            f"- {dataset}: records={output['record_count']} "
            f"source={output['source']} subject={first['subject']} "
            f"as_of={first['as_of']} cache_hit={output['cache_hit']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
