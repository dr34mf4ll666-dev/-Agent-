"""Offline working-memory, eviction, snapshot and restore demonstration."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (  # noqa: E402
    Action,
    AgentRequest,
    CognitiveLoopRunner,
    JSONSchemaValidator,
    JsonWorkingMemoryStore,
    MemoryKind,
    Observation,
    Plan,
    Reflection,
    ReflectionDecision,
    ToolRegistry,
    WorkingMemory,
)


class AddTool:
    name = "math.add"

    def run(self, arguments: Mapping[str, Any]) -> int | float:
        return arguments["left"] + arguments["right"]


class MemoryRepairAgent:
    name = "memory_repair_agent"

    def create_plan(self, request: AgentRequest) -> Plan:
        return Plan(
            goal=request.task,
            steps=("读取原始参数", "调用工具", "根据 Observation 修正或完成"),
        )

    def choose_action(self, state) -> Action:
        failed_before = any(
            entry.kind is MemoryKind.OBSERVATION
            and entry.data.get("success") is False
            for entry in state.memory.entries
        )
        left = 4 if failed_before else "4"
        return Action(
            tool="math.add",
            arguments={"left": left, "right": 5},
            rationale=(
                "工作记忆显示上次参数失败，改为数字"
                if failed_before
                else "首次尝试原始参数"
            ),
        )

    def reflect(self, state, observation: Observation) -> Reflection:
        if not observation.success:
            return Reflection(
                decision=ReflectionDecision.REVISE,
                reason="Observation 失败，下一步根据工作记忆修正参数",
            )
        return Reflection(
            decision=ReflectionDecision.COMPLETE,
            reason="工作记忆中的最新 Observation 已成功",
            final_answer=f"计算结果是 {observation.output}",
        )


def tool_guardrail() -> JSONSchemaValidator:
    return JSONSchemaValidator(
        input_schema={
            "type": "object",
            "required": ["left", "right"],
            "properties": {
                "left": {"type": "number"},
                "right": {"type": "number"},
            },
            "additionalProperties": False,
        },
        input_path="context.arguments",
        output_schema={
            "type": "object",
            "required": ["tool", "output"],
            "properties": {
                "tool": {"const": "math.add"},
                "output": {"type": "number"},
            },
            "additionalProperties": False,
        },
        output_path="metadata.observation",
        name="memory_demo_schema",
    )


def run_demo(snapshot_path: Path) -> int:
    memory = WorkingMemory(capacity=5)
    store = JsonWorkingMemoryStore(snapshot_path)
    runner = CognitiveLoopRunner(
        agent=MemoryRepairAgent(),
        tools=ToolRegistry([AddTool()]),
        tool_guardrails=(tool_guardrail(),),
        working_memory=memory,
        memory_store=store,
        max_steps=2,
    )
    result = runner.run(AgentRequest(task="计算 4 + 5"))
    restored = WorkingMemory.restore(store.load())
    view = restored.view()

    print("=== 工作记忆离线演示 ===")
    print("最终回答:", result.response.content)
    print("容量:", view.capacity)
    print("已淘汰:", view.dropped_count)
    print("恢复后的记忆:")
    for entry in view.entries:
        print(
            f"- #{entry.sequence} {entry.kind.value} "
            f"step={entry.step}: {entry.summary}"
        )
    print("JSON 快照:", snapshot_path.resolve())
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行工作记忆离线演示。")
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=PROJECT_ROOT / "checkpoints" / "working_memory.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run_demo(args.snapshot)


if __name__ == "__main__":
    raise SystemExit(main())
