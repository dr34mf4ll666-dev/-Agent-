"""Repository entry for the complete D4 final delivery verification."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(["d4-verify", *sys.argv[1:]]))
