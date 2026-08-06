"""A4 Model Gateway demo: offline by default, explicit opt-in for a live call."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    DeepSeekChatAdapter,
    MockModelAdapter,
    ModelGateway,
    ModelGatewayConfigurationError,
    ModelRequest,
    ModelRetryPolicy,
    ModelUsage,
    OpenAIResponsesAdapter,
)


def build_demo_schema(expected_mode: str) -> dict:
    """Build the schema used by one demo mode."""

    if expected_mode not in {"offline", "live"}:
        raise ValueError("expected_mode must be offline or live")
    return {
        "type": "object",
        "properties": {
            "message": {"type": "string", "minLength": 1},
            "mode": {"type": "string", "enum": [expected_mode]},
        },
        "required": ["message", "mode"],
        "additionalProperties": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行统一 Model Gateway 演示")
    parser.add_argument(
        "--live",
        action="store_true",
        help="显式使用所选供应商的 API Key 发起一次真实模型请求",
    )
    parser.add_argument(
        "--provider",
        choices=("deepseek", "openai"),
        default=os.environ.get("LIVE_LLM_PROVIDER", "deepseek"),
        help="--live 使用的真实模型供应商，默认 deepseek",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="真实调用使用的模型名；不填时按供应商选择当前项目默认值",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    expected_mode = "live" if args.live else "offline"
    if args.live:
        if args.provider == "deepseek":
            model = args.model or os.environ.get(
                "DEEPSEEK_MODEL",
                "deepseek-v4-flash",
            )
            adapter_factory = DeepSeekChatAdapter.from_env
        else:
            model = args.model or os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
            adapter_factory = OpenAIResponsesAdapter.from_env
        try:
            adapter = adapter_factory(model=model)
        except ModelGatewayConfigurationError as error:
            print(f"无法启动真实演示: {error}")
            print("请只在本地 PowerShell 设置对应供应商的 API Key，不要写进仓库。")
            return 2
        prompt = "Return a short confirmation that the Model Gateway is connected."
    else:
        adapter = MockModelAdapter(
            content='{"message":"Model Gateway 离线链路正常","mode":"offline"}',
            structured_output={
                "message": "Model Gateway 离线链路正常",
                "mode": "offline",
            },
            usage=ModelUsage(input_tokens=12, output_tokens=9, total_tokens=21),
        )
        prompt = "验证 Model Gateway 离线链路"

    gateway = ModelGateway(
        adapter,
        retry_policy=ModelRetryPolicy(
            max_attempts=2,
            timeout_seconds=20,
            initial_backoff_seconds=0.25,
        ),
    )
    result = gateway.generate(
        ModelRequest(
            prompt=prompt,
            system_prompt=(
                "Return only JSON matching the supplied schema. "
                f"Set the mode field exactly to {expected_mode}."
            ),
            response_schema=build_demo_schema(expected_mode),
            schema_name="gateway_check",
            max_output_tokens=80,
        )
    )

    response = result.response
    print("=== A4 统一 Model Gateway 演示 ===")
    print(f"provider: {response.provider}")
    print(f"model: {response.model}")
    print(f"response_id: {response.response_id or '-'}")
    print(f"status: {response.status}")
    print(f"attempts: {response.attempts}")
    print(
        "tokens: "
        f"input={response.usage.input_tokens}, "
        f"output={response.usage.output_tokens}, "
        f"total={response.usage.total_tokens}"
    )
    print(f"latency_ms: {response.latency_ms}")
    print(f"structured_output: {response.structured_output}")
    print("trace:")
    for event in result.trace:
        suffix = f" ({event.detail})" if event.detail else ""
        print(f"- {event.event} [attempt={event.attempt}]{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
