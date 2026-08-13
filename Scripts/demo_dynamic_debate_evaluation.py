"""Run the fixed constrained dynamic-debate evaluation through the stable CLI."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.cli import main as platform_main  # noqa: E402


def main() -> int:
    return platform_main(["debate-eval", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
