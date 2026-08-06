"""A5 demo: reuse the generic platform for non-financial local research."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    DeepSeekChatAdapter,
    GraphExecutionError,
    ModelGateway,
    ModelGatewayConfigurationError,
    ModelRetryPolicy,
)
from agent_platform.research import (
    NonFinancialResearchRuntime,
    build_offline_research_gateway,
    load_documents,
)


DEFAULT_TOPIC = "Python 代码评审实践"
DEFAULT_DOCUMENTS = PROJECT_ROOT / "tests" / "fixtures" / "research_documents.json"
DEFAULT_WORKFLOW = PROJECT_ROOT / "Workflow" / "examples" / "non_financial_research.yaml"
DEFAULT_CHECKPOINT = PROJECT_ROOT / ".runtime" / "a5-research" / "checkpoint.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 A5 非金融资料研究演示")
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--timestamp", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--live",
        action="store_true",
        help="显式使用 DeepSeek；默认 Mock 离线运行",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="DeepSeek 模型名；默认读取 DEEPSEEK_MODEL 或使用项目默认值",
    )
    parser.add_argument(
        "--verify-recovery",
        action="store_true",
        help="先模拟综合节点失败，再从 Checkpoint 恢复",
    )
    return parser


def _build_gateway(args: argparse.Namespace) -> ModelGateway:
    if not args.live:
        return build_offline_research_gateway(args.topic)
    model = args.model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    adapter = DeepSeekChatAdapter.from_env(model=model)
    return ModelGateway(
        adapter,
        retry_policy=ModelRetryPolicy(
            max_attempts=2,
            timeout_seconds=30,
            initial_backoff_seconds=0.25,
        ),
    )


def main() -> int:
    args = build_parser().parse_args()
    try:
        gateway = _build_gateway(args)
    except ModelGatewayConfigurationError as error:
        print(f"无法启动真实演示: {error}")
        print("请只在当前 PowerShell 设置 DEEPSEEK_API_KEY，不要写进仓库。")
        return 2

    documents = load_documents(args.documents)
    run_timestamp = args.timestamp or datetime.now().astimezone().isoformat(timespec="seconds")
    recovered = False

    if args.verify_recovery:
        failing_runtime = NonFinancialResearchRuntime(
            gateway=gateway,
            documents=documents,
            workflow_path=args.workflow,
            checkpoint_path=args.checkpoint,
            fail_synthesis_attempts=1,
        )
        try:
            failing_runtime.run(topic=args.topic, run_timestamp=run_timestamp)
        except GraphExecutionError as error:
            print("预期故障: synthesize 节点失败，Checkpoint 已保存")
            print(f"- completed_before_failure: {list(error.execution_order)}")
        else:
            print("恢复演示失败: 预期故障没有发生")
            return 3
        recovered = True

    runtime = NonFinancialResearchRuntime(
        gateway=gateway,
        documents=documents,
        workflow_path=args.workflow,
        checkpoint_path=args.checkpoint,
    )
    result = runtime.run(
        topic=args.topic,
        run_timestamp=run_timestamp,
        resume=recovered,
    )
    state = result.state.to_dict()
    retrieval = state["retrieval"]
    report = state["report"]
    model_calls = [*retrieval["model_calls"], *state["report_model_calls"]]
    total_tokens = sum(call["tokens"]["total"] for call in model_calls)

    print("=== A5 非金融资料研究演示 ===")
    print(f"mode: {'live' if args.live else 'offline'}")
    print(f"topic: {report['topic']}")
    print(f"execution_order: {list(result.execution_order)}")
    print(f"statuses: {result.statuses}")
    print(f"loop_steps: {retrieval['loop_steps']}")
    print(f"allowed_tools: {retrieval['allowed_tools']}")
    print(f"working_memory_entries: {retrieval['working_memory_entries']}")
    print(f"evidence_count: {state['evidence_count']}")
    for item in state["evidence"]:
        print(f"- {item['evidence_id']} {item['title']} ({item['source']})")
    print(f"summary: {report['summary']}")
    for finding in report["findings"]:
        print(f"- finding: {finding['claim']} evidence={finding['evidence_ids']}")
    print(f"report_source: {report['source']}")
    print(f"model_calls: {len(model_calls)}; total_tokens: {total_tokens}")
    print(f"checkpoint: {args.checkpoint}")
    if recovered:
        print(f"recovery_node_calls: {runtime.node_calls}")
    print("graph_trace:")
    for event in result.trace:
        suffix = f" ({event.detail})" if event.detail else ""
        print(f"- {event.event} {event.node} [attempt={event.attempt}]{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
