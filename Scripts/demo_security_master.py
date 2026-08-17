"""Show the versioned securities master and optionally verify one non-bank Graph."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.security_master import DEFAULT_SECURITY_MASTER  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P9 证券主数据与跨行业扩展演示")
    parser.add_argument(
        "--verify-non-bank",
        action="store_true",
        help="调用真实只读完整 Graph 验证五粮液，不保存报告文件",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    records = DEFAULT_SECURITY_MASTER.customer_records()
    counts = Counter(record.industry for record in records)

    print("=== P9 证券主数据演示 ===")
    print(f"主数据版本（catalog_version）: {DEFAULT_SECURITY_MASTER.catalog_version}")
    print(f"客户正式目录: {len(records)} 只")
    print("行业分布（industry）:")
    for industry, count in sorted(counts.items()):
        print(f"- {industry}: {count} 只")
    print("目录字段与能力:")
    for record in records:
        modes = "/".join(record.available_modes)
        snapshot = str(record.snapshot.get("status", "unknown"))
        print(
            f"- {record.name}（{record.symbol}） | {record.exchange} | "
            f"行业={record.industry} | 数据={modes} | 快照={snapshot} | "
            f"完整Graph={'可用' if record.capabilities.get('full_graph') else '不可用'}"
        )
    print("安全边界: 只有 verified=true 且 customer_visible=true 的标的进入上述目录。")

    if not arguments.verify_non_bank:
        print("提示: 加 --verify-non-bank 可重新执行五粮液真实只读完整 Graph 验收。")
        return 0

    from demo_financial_graph import main as run_financial_graph

    result = run_financial_graph([
        "--live",
        "--symbol",
        "sz000858",
        "--sector",
        "酿酒行业",
        "--start-date",
        "20240101",
        "--end-date",
        "20260817",
        "--timeout",
        "60",
        "--attempts",
        "1",
    ])
    if result != 0:
        print("P9 非银行真实端到端验收失败；该标的不应进入正式目录。")
        return result
    print("P9 非银行真实端到端验收通过：sz000858 / 酿酒行业。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
