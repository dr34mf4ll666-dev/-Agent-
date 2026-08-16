"""Start the local A-D Agent platform Web control console."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.dashboard import DashboardError, serve_dashboard  # noqa: E402
from agent_platform.dashboard_startup import configure_deepseek_for_dashboard  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动通用 Agent 平台 Web 控制台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    parser.add_argument("--port", type=int, default=8765, help="本机端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--no-key-prompt",
        action="store_true",
        help="不询问 DeepSeek API Key，直接使用已有环境变量或固定格式",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    selection = configure_deepseek_for_dashboard(
        prompt_enabled=not args.no_key_prompt,
        interactive=sys.stdin.isatty(),
    )
    print(selection.message)
    try:
        serve_dashboard(
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
        )
    except DashboardError as error:
        print(f"启动失败: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
