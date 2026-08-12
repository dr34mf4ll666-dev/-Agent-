"""Run the complete product acceptance in one terminal command."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.product_acceptance import (  # noqa: E402
    ProductAcceptanceRuntime,
    print_product_acceptance,
)


def main() -> int:
    report = ProductAcceptanceRuntime.from_project(PROJECT_ROOT).run()
    print_product_acceptance(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
