"""Run the full A2 graph-engineering feature set offline."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (  # noqa: E402
    GraphRunner,
    GraphVisualizer,
    GraphWorkflowLoader,
    JsonCheckpointStore,
    NodeRegistry,
)


DEFAULT_WORKFLOW = PROJECT_ROOT / "Workflow" / "examples" / "parallel_analysis.yaml"


def build_registry() -> tuple[NodeRegistry, dict[str, int]]:
    attempts = {"fundamental": 0, "technical": 0}

    def prepare(state):
        return {"subject": "A2 Graph Engineering", "prepared": True}

    def fundamental(state):
        attempts["fundamental"] += 1
        time.sleep(0.02)
        if attempts["fundamental"] == 1:
            raise RuntimeError("offline transient failure")
        return {"fundamental_score": 72}

    def technical(state):
        attempts["technical"] += 1
        time.sleep(0.02)
        return {"technical_score": 64}

    def synthesize(state):
        score = (state["fundamental_score"] + state["technical_score"]) // 2
        return {"summary_score": score, "done": True}

    return (
        NodeRegistry(
            {
                "prepare": prepare,
                "fundamental": fundamental,
                "technical": technical,
                "synthesize": synthesize,
            }
        ),
        attempts,
    )


def run_demo(workflow_path: Path, checkpoint_path: Path, mermaid_path: Path) -> int:
    registry, handler_attempts = build_registry()
    graph = GraphWorkflowLoader(registry).load(workflow_path)
    runner = GraphRunner(
        graph,
        checkpoint_store=JsonCheckpointStore(checkpoint_path),
    )
    result = runner.run({"request_id": "a2-offline-demo"})

    mermaid = GraphVisualizer().render_mermaid(graph, result=result)
    mermaid_path.parent.mkdir(parents=True, exist_ok=True)
    mermaid_path.write_text(mermaid, encoding="utf-8")

    print("=== A2 Graph Engineering 离线演示 ===")
    print("工作流:", workflow_path.resolve())
    print("调度策略:", graph.execution.strategy)
    for event in result.trace:
        if event.event == "graph.wave.started":
            print("并行波次:", event.detail)
    print("执行顺序:", " -> ".join(result.execution_order))
    print("节点尝试次数:", json.dumps(result.attempts, ensure_ascii=False))
    print("处理函数调用:", json.dumps(handler_attempts, ensure_ascii=False))
    print("熔断器:", json.dumps(result.circuit_breakers, ensure_ascii=False))
    print("最终状态:", json.dumps(result.state.to_dict(), ensure_ascii=False))
    print("Checkpoint:", checkpoint_path.resolve())
    print("Mermaid:", mermaid_path.resolve())
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行完整 A2 Graph 工程演示。")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=PROJECT_ROOT / "checkpoints" / "a2_graph.json",
    )
    parser.add_argument(
        "--mermaid",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "a2_graph.mmd",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_demo(args.workflow, args.checkpoint, args.mermaid)


if __name__ == "__main__":
    raise SystemExit(main())
