import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    Action,
    AgentRequest,
    CognitiveContractError,
    CognitiveLoopExecutionError,
    CognitiveLoopRunner,
    CognitiveMaxStepsExceeded,
    JSONSchemaValidator,
    Plan,
    Reflection,
    ReflectionDecision,
    ToolConfigurationError,
    ToolRegistry,
)


class ScriptedCognitiveAgent:
    name = "scripted_cognitive_agent"

    def __init__(self, actions, reflections):
        self._actions = iter(actions)
        self._reflections = iter(reflections)

    def create_plan(self, request):
        return Plan(goal=request.task, steps=("调用工具", "检查结果"))

    def choose_action(self, state):
        return next(self._actions)

    def reflect(self, state, observation):
        return next(self._reflections)


class AddTool:
    name = "math.add"

    def __init__(self):
        self.call_count = 0

    def run(self, arguments):
        self.call_count += 1
        return arguments["left"] + arguments["right"]


class FlakyAddTool(AddTool):
    def run(self, arguments):
        self.call_count += 1
        if self.call_count == 1:
            raise RuntimeError("temporary tool failure")
        return arguments["left"] + arguments["right"]


class CognitiveLoopTests(unittest.TestCase):
    def test_rejects_invalid_contracts_and_duplicate_tool_names(self):
        with self.assertRaises(CognitiveContractError):
            Plan(goal="", steps=("run",))
        with self.assertRaises(CognitiveContractError):
            Action(tool="", arguments={})
        with self.assertRaises(CognitiveContractError):
            Reflection(
                decision=ReflectionDecision.COMPLETE,
                reason="done",
            )
        with self.assertRaises(ToolConfigurationError):
            ToolRegistry([AddTool(), AddTool()])

    def test_completes_plan_action_observation_reflection_cycle(self):
        tool = AddTool()
        agent = ScriptedCognitiveAgent(
            actions=[Action(tool="math.add", arguments={"left": 1, "right": 2})],
            reflections=[
                Reflection(
                    decision=ReflectionDecision.COMPLETE,
                    reason="计算结果已经满足目标",
                    final_answer="计算结果是 3",
                )
            ],
        )
        runner = CognitiveLoopRunner(
            agent=agent,
            tools=ToolRegistry([tool]),
            tool_guardrails=(self._tool_schema_guardrail(),),
            max_steps=2,
        )

        result = runner.run(AgentRequest(task="计算 1 + 2"))

        self.assertEqual(result.response.content, "计算结果是 3")
        self.assertTrue(result.state.done)
        self.assertEqual(result.state.step_count, 1)
        self.assertEqual(result.state.plan.goal, "计算 1 + 2")
        self.assertEqual(result.state.observations[0].output, 3)
        self.assertTrue(result.state.observations[0].success)
        self.assertEqual(tool.call_count, 1)
        harness_events = [
            event.event
            for event in result.tool_records[0].harness_traces[0]
        ]
        self.assertIn("guardrail.input.passed", harness_events)
        self.assertIn("guardrail.output.passed", harness_events)

    def test_unknown_tool_becomes_observation_and_agent_can_revise(self):
        tool = AddTool()
        agent = ScriptedCognitiveAgent(
            actions=[
                Action(tool="math.multiply", arguments={"left": 1, "right": 2}),
                Action(tool="math.add", arguments={"left": 1, "right": 2}),
            ],
            reflections=[
                Reflection(
                    decision=ReflectionDecision.REVISE,
                    reason="工具不在允许列表，改用已注册工具",
                ),
                Reflection(
                    decision=ReflectionDecision.COMPLETE,
                    reason="已取得有效结果",
                    final_answer="计算结果是 3",
                ),
            ],
        )
        runner = CognitiveLoopRunner(
            agent=agent,
            tools=ToolRegistry([tool]),
            max_steps=2,
        )

        result = runner.run(AgentRequest(task="完成一次受控计算"))

        first_observation = result.state.observations[0]
        self.assertFalse(first_observation.success)
        self.assertIn("UnknownToolError", first_observation.error)
        self.assertEqual(
            result.state.reflections[0].decision,
            ReflectionDecision.REVISE,
        )
        self.assertTrue(result.state.observations[1].success)
        self.assertEqual(tool.call_count, 1)

    def test_retries_same_action_after_tool_failure(self):
        tool = FlakyAddTool()
        agent = ScriptedCognitiveAgent(
            actions=[Action(tool="math.add", arguments={"left": 2, "right": 3})],
            reflections=[
                Reflection(
                    decision=ReflectionDecision.COMPLETE,
                    reason="重试后工具成功",
                    final_answer="计算结果是 5",
                )
            ],
        )
        runner = CognitiveLoopRunner(
            agent=agent,
            tools=ToolRegistry([tool]),
            max_steps=1,
            max_tool_retries=1,
        )

        result = runner.run(AgentRequest(task="计算 2 + 3"))

        record = result.tool_records[0]
        self.assertEqual(record.attempts, 2)
        self.assertEqual(len(record.harness_traces), 2)
        self.assertTrue(record.observation.success)
        self.assertEqual(record.observation.output, 5)
        self.assertEqual(tool.call_count, 2)
        self.assertIn("agent.failed", [event.event for event in record.harness_traces[0]])
        self.assertIn(
            "postflight.passed",
            [event.event for event in record.harness_traces[1]],
        )

    def test_guardrail_failure_prevents_tool_call_and_can_be_revised(self):
        tool = AddTool()
        agent = ScriptedCognitiveAgent(
            actions=[
                Action(tool="math.add", arguments={"left": "1", "right": 2}),
                Action(tool="math.add", arguments={"left": 1, "right": 2}),
            ],
            reflections=[
                Reflection(
                    decision=ReflectionDecision.REVISE,
                    reason="参数类型错误，修正后重试",
                ),
                Reflection(
                    decision=ReflectionDecision.COMPLETE,
                    reason="修正后的结果有效",
                    final_answer="计算结果是 3",
                ),
            ],
        )
        runner = CognitiveLoopRunner(
            agent=agent,
            tools=ToolRegistry([tool]),
            tool_guardrails=(self._tool_schema_guardrail(),),
            max_steps=2,
        )

        result = runner.run(AgentRequest(task="修正参数并计算"))

        self.assertFalse(result.state.observations[0].success)
        self.assertIn("GuardrailViolation", result.state.observations[0].error)
        first_trace = result.tool_records[0].harness_traces[0]
        self.assertIn("preflight.failed", [event.event for event in first_trace])
        self.assertEqual(tool.call_count, 1)
        self.assertTrue(result.state.observations[1].success)

    def test_stops_safely_when_reflection_never_completes(self):
        tool = AddTool()
        agent = ScriptedCognitiveAgent(
            actions=[
                Action(tool="math.add", arguments={"left": 1, "right": 1}),
                Action(tool="math.add", arguments={"left": 2, "right": 2}),
            ],
            reflections=[
                Reflection(
                    decision=ReflectionDecision.CONTINUE,
                    reason="还需要继续",
                ),
                Reflection(
                    decision=ReflectionDecision.CONTINUE,
                    reason="仍未完成",
                ),
            ],
        )
        runner = CognitiveLoopRunner(
            agent=agent,
            tools=ToolRegistry([tool]),
            max_steps=2,
        )

        with self.assertRaises(CognitiveLoopExecutionError) as raised:
            runner.run(AgentRequest(task="永不主动完成的任务"))

        error = raised.exception
        self.assertIsInstance(error.cause, CognitiveMaxStepsExceeded)
        self.assertEqual(error.state.step_count, 2)
        self.assertEqual(len(error.state.observations), 2)
        self.assertEqual(len(error.tool_records), 2)
        self.assertEqual(error.trace[-1].event, "cognitive_loop.max_steps_exceeded")

    @staticmethod
    def _tool_schema_guardrail():
        return JSONSchemaValidator(
            input_path="context.arguments",
            input_schema={
                "type": "object",
                "required": ["left", "right"],
                "properties": {
                    "left": {"type": "number"},
                    "right": {"type": "number"},
                },
                "additionalProperties": False,
            },
            output_path="metadata.observation",
            output_schema={
                "type": "object",
                "required": ["tool", "output"],
                "properties": {
                    "tool": {"type": "string"},
                    "output": {"type": "number"},
                },
                "additionalProperties": False,
            },
            name="math_tool_schema",
        )


if __name__ == "__main__":
    unittest.main()
