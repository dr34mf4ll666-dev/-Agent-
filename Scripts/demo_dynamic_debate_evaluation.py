"""Run the fixed constrained dynamic-debate evaluation through the stable CLI."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.cli import main as platform_main  # noqa: E402


DEFAULT_LIVE_OUTPUT = ".runtime/llm-evaluation/deepseek-fixed-v1.json"


def build_arguments(arguments: list[str]) -> list[str]:
    """Keep one canonical raw-result file for every live fixed evaluation."""

    forwarded = list(arguments)
    if "--live" in forwarded and "--output" not in forwarded:
        forwarded.extend(["--output", DEFAULT_LIVE_OUTPUT])
    return ["debate-eval", *forwarded]


def main() -> int:
    return platform_main(build_arguments(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
