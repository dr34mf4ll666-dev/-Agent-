"""可直接运行的 Graph 条件分支与 Checkpoint 恢复演示。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (  # noqa: E402
    GraphDefinition,
    GraphEdge,
    GraphExecutionError,
    GraphRunner,
    JsonCheckpointStore,
)


EDGE_SCHEMA = {"type": "object"}


def edge(source, target, *, condition=None, condition_label=""):
    return GraphEdge(
        source,
        target,
        condition=condition,
        output_schema=EDGE_SCHEMA,
        input_schema=EDGE_SCHEMA,
        condition_label=condition_label,
    )


def build_demo_graph(route_choice: str, *, simulate_failure: bool) -> GraphDefinition:
    """创建一个包含条件分支和一次可恢复故障的离线 Graph。"""

    attempts = {"recoverable": 0}

    def prepare(state):
        print("[节点] prepare：准备离线演示数据")
        return {"prepared": True}

    def route(state):
        print(f"[节点] route：选择 {route_choice} 分支")
        return {"route": route_choice}

    def approved(state):
        print("[节点] approved：处理通过分支")
        return {"decision": "approved"}

    def rejected(state):
        print("[节点] rejected：处理拒绝分支")
        return {"decision": "rejected"}

    def recoverable(state):
        attempts["recoverable"] += 1
        print(f"[节点] recoverable：第 {attempts['recoverable']} 次尝试")
        if simulate_failure and attempts["recoverable"] == 1:
            raise RuntimeError("演示用临时故障")
        return {"recovered": attempts["recoverable"] > 1}

    def finish(state):
        print("[节点] finish：汇总最终结果")
        return {"done": True}

    return GraphDefinition(
        start="prepare",
        nodes={
            "prepare": prepare,
            "route": route,
            "approved": approved,
            "rejected": rejected,
            "recoverable": recoverable,
            "finish": finish,
        },
        edges=(
            edge("prepare", "route"),
            edge(
                "route",
                "approved",
                condition=lambda state: state["route"] == "approved",
                condition_label="route == approved",
            ),
            edge(
                "route",
                "rejected",
                condition=lambda state: state["route"] == "rejected",
                condition_label="route == rejected",
            ),
            edge("approved", "recoverable"),
            edge("recoverable", "finish"),
            edge("rejected", "finish"),
        ),
    )


def run_demo(
    *,
    route_choice: str,
    checkpoint_path: Path,
    simulate_failure: bool,
) -> int:
    """执行演示，并在预期故障后自动从 Checkpoint 恢复。"""

    print("=== 通用 Agent 平台：Graph 故障恢复演示 ===")
    print(f"Checkpoint: {checkpoint_path.resolve()}")

    graph = build_demo_graph(route_choice, simulate_failure=simulate_failure)
    runner = GraphRunner(
        graph,
        checkpoint_store=JsonCheckpointStore(checkpoint_path),
    )

    try:
        result = runner.run({"request_id": "demo-graph-001"})
    except GraphExecutionError as error:
        if error.statuses.get("recoverable") != "failed":
            raise
        print(f"[演示] 首次执行在 recoverable 节点失败：{error.cause}")
        print("[演示] 正在从 Checkpoint 恢复")
        result = runner.run(resume=True)

    print("\n=== 执行完成 ===")
    print(f"执行顺序: {' -> '.join(result.execution_order)}")
    print("节点状态:")
    for node_name, status in result.statuses.items():
        print(f"- {node_name}: {status}")
    print("最终状态:")
    print(json.dumps(result.state.to_dict(), ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行 Graph 条件分支和 Checkpoint 故障恢复演示。"
    )
    parser.add_argument(
        "--route",
        choices=("approved", "rejected"),
        default="approved",
        help="选择演示使用的条件分支，默认 approved。",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=PROJECT_ROOT / "checkpoints" / "demo_graph.json",
        help="Checkpoint JSON 文件路径。",
    )
    parser.add_argument(
        "--no-failure",
        action="store_true",
        help="关闭首次故障，直接完成 Graph。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_demo(
        route_choice=args.route,
        checkpoint_path=args.checkpoint,
        simulate_failure=not args.no_failure,
    )


if __name__ == "__main__":
    raise SystemExit(main())
