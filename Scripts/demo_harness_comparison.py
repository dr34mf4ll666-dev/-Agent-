"""D3 Harness 价值量化的独立中文验收入口。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(["d3-compare", *sys.argv[1:]]))
