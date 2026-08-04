"""离线演示 Plan-Action-Observation-Reflection 受控工具闭环。"""

from __future__ import annotations

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
    Observation,
    Plan,
    Reflection,
    ReflectionDecision,
    ToolRegistry,
)


class AddTool:
    name = "math.add"

    def run(self, arguments: Mapping[str, Any]) -> int | float:
        return arguments["left"] + arguments["right"]


class RepairingCalculatorAgent:
    """先尝试原参数；观察到校验失败后，确定性地修正数字字符串。"""

    name = "repairing_calculator"

    def create_plan(self, request: AgentRequest) -> Plan:
        return Plan(
            goal=request.task,
            steps=("读取参数", "调用受控加法工具", "检查结果并作答"),
        )

    def choose_action(self, state) -> Action:
        arguments = {
            "left": state.request.context["left"],
            "right": state.request.context["right"],
        }
        rationale = "先使用请求中的原始参数"
        if state.observations and not state.observations[-1].success:
            arguments = {
                key: self._to_number(value)
                for key, value in arguments.items()
            }
            rationale = "上次参数校验失败，将数字字符串转换为数字后重试"
        return Action(
            tool="math.add",
            arguments=arguments,
            rationale=rationale,
        )

    def reflect(self, state, observation: Observation) -> Reflection:
        if not observation.success:
            return Reflection(
                decision=ReflectionDecision.REVISE,
                reason="工具没有产生通过校验的 Observation，需要修正参数",
            )
        return Reflection(
            decision=ReflectionDecision.COMPLETE,
            reason="受控工具已返回通过后置校验的结果",
            final_answer=f"计算结果是 {observation.output}",
        )

    @staticmethod
    def _to_number(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return float(value)
        return value


def build_tool_guardrail() -> JSONSchemaValidator:
    number_arguments = {
        "type": "object",
        "required": ["left", "right"],
        "properties": {
            "left": {"type": "number"},
            "right": {"type": "number"},
        },
        "additionalProperties": False,
    }
    observation = {
        "type": "object",
        "required": ["tool", "output"],
        "properties": {
            "tool": {"const": "math.add"},
            "output": {"type": "number"},
        },
        "additionalProperties": False,
    }
    return JSONSchemaValidator(
        input_schema=number_arguments,
        input_path="context.arguments",
        output_schema=observation,
        output_path="metadata.observation",
        name="math_add_schema",
    )


def main() -> int:
    tools = ToolRegistry([AddTool()])
    runner = CognitiveLoopRunner(
        agent=RepairingCalculatorAgent(),
        tools=tools,
        tool_guardrails=(build_tool_guardrail(),),
        max_steps=2,
    )
    result = runner.run(
        AgentRequest(
            task="计算 4 + 5",
            context={"left": "4", "right": 5},
        )
    )

    print("=== 认知 Loop 离线演示 ===")
    print("Plan:", " -> ".join(result.state.plan.steps))
    for index, record in enumerate(result.tool_records, start=1):
        reflection = result.state.reflections[index - 1]
        print(f"\nStep {index}")
        print("Action:", record.action.tool, dict(record.action.arguments))
        if record.observation.success:
            print("Observation: passed", record.observation.output)
        else:
            print("Observation: failed", record.observation.error)
        print("Reflection:", reflection.decision.value, reflection.reason)
        print("Harness:")
        for event in record.harness_traces[-1]:
            suffix = f" ({event.detail})" if event.detail else ""
            print(f"- {event.event}{suffix}")

    print("\nFinal:", result.response.content)
    print("Allowed tools:", ", ".join(tools.names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
