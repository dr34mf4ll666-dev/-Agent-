import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core.contracts import (
    AgentRequest,
    AgentResponse,
    GuardrailViolation,
    HarnessExecutionError,
)
from agent_platform.core.echo import EchoAgent
from agent_platform.core.harness import AgentHarness


class CountingAgent:
    name = "counting"

    def __init__(self, response=None, error=None):
        self.calls = 0
        self.response = response or AgentResponse(content="ok")
        self.error = error

    def run(self, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


class RejectInputGuardrail:
    name = "reject_input"

    def check_input(self, request):
        raise GuardrailViolation("input rejected by test guardrail")

    def check_output(self, response):
        return None


class EchoAndHarnessTests(unittest.TestCase):
    def test_echo_agent_returns_the_requested_task(self):
        response = EchoAgent().run(AgentRequest(task="hello"))

        self.assertEqual(response.content, "hello")
        self.assertEqual(response.metadata["agent"], "echo")

    def test_harness_returns_response_and_ordered_trace(self):
        result = AgentHarness(EchoAgent()).run(AgentRequest(task="hello"))

        self.assertEqual(result.response.content, "hello")
        self.assertEqual(
            [event.event for event in result.trace],
            [
                "preflight.started",
                "preflight.passed",
                "agent.started",
                "agent.finished",
                "postflight.passed",
            ],
        )
        self.assertTrue(all(event.agent == "echo" for event in result.trace))

    def test_harness_rejects_blank_task_before_calling_agent(self):
        agent = CountingAgent()

        with self.assertRaises(HarnessExecutionError) as raised:
            AgentHarness(agent).run(AgentRequest(task="   "))

        self.assertIsInstance(raised.exception.cause, GuardrailViolation)
        self.assertEqual(agent.calls, 0)
        self.assertEqual(
            [event.event for event in raised.exception.trace],
            ["preflight.started", "preflight.failed"],
        )

    def test_custom_guardrail_can_block_before_agent_runs(self):
        agent = CountingAgent()

        with self.assertRaises(HarnessExecutionError) as raised:
            AgentHarness(agent, guardrails=[RejectInputGuardrail()]).run(
                AgentRequest(task="hello")
            )

        self.assertIsInstance(raised.exception.cause, GuardrailViolation)
        self.assertEqual(agent.calls, 0)
        self.assertEqual(raised.exception.trace[-1].event, "preflight.failed")

    def test_harness_records_agent_failure(self):
        agent = CountingAgent(error=RuntimeError("agent failed"))

        with self.assertRaises(HarnessExecutionError) as raised:
            AgentHarness(agent).run(AgentRequest(task="hello"))

        self.assertEqual(str(raised.exception.cause), "agent failed")
        self.assertEqual(
            [event.event for event in raised.exception.trace],
            [
                "preflight.started",
                "preflight.passed",
                "agent.started",
                "agent.failed",
            ],
        )


if __name__ == "__main__":
    unittest.main()
