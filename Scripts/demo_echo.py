"""Directly runnable Echo Agent and Harness health check."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import AgentHarness, AgentRequest, EchoAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 Echo Agent Harness 演示")
    parser.add_argument("--task", default="hello agent platform")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = AgentHarness(EchoAgent()).run(AgentRequest(task=args.task))

    print("=== Echo Agent Harness 演示 ===")
    print(f"input: {args.task}")
    print(f"output: {result.response.content}")
    print(f"agent: {result.response.metadata['agent']}")
    print("trace:")
    for event in result.trace:
        suffix = f" ({event.detail})" if event.detail else ""
        print(f"- {event.event} [{event.agent}]{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
