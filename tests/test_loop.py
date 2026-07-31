import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core.contracts import AgentRequest, AgentResponse
from agent_platform.core.harness import AgentHarness
from agent_platform.core.loop import (
    LoopExecutionError,
    LoopMaxStepsExceeded,
    LoopRunner,
)


class ScriptedAgent:
    name = "scripted"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.requests = []

    def run(self, request):
        self.calls += 1
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def is_done(response):
    return response.metadata.get("done", False)


class LoopRunnerTests(unittest.TestCase):
    def test_loop_repeats_until_completion_and_keeps_history(self):
        agent = ScriptedAgent(
            [
                AgentResponse(content="still working", metadata={"done": False}),
                AgentResponse(content="finished", metadata={"done": True}),
            ]
        )
        runner = LoopRunner(
            AgentHarness(agent),
            completion_checker=is_done,
            max_steps=3,
        )

        result = runner.run(AgentRequest(task="complete the task"))

        self.assertEqual(result.response.content, "finished")
        self.assertEqual(result.state.step_count, 2)
        self.assertTrue(result.state.done)
        self.assertEqual(len(result.state.history), 2)
        self.assertEqual(agent.calls, 2)
        self.assertEqual(agent.requests[0].context["history"], ())
        self.assertEqual(
            agent.requests[1].context["history"][0].content,
            "still working",
        )
        self.assertEqual(result.trace[-1].event, "loop.completed")

    def test_loop_stops_when_max_steps_is_reached(self):
        agent = ScriptedAgent(
            [
                AgentResponse(content="not done", metadata={"done": False}),
                AgentResponse(content="still not done", metadata={"done": False}),
            ]
        )
        runner = LoopRunner(
            AgentHarness(agent),
            completion_checker=is_done,
            max_steps=2,
        )

        with self.assertRaises(LoopExecutionError) as raised:
            runner.run(AgentRequest(task="never finish"))

        self.assertIsInstance(raised.exception.cause, LoopMaxStepsExceeded)
        self.assertEqual(raised.exception.state.step_count, 2)
        self.assertEqual(agent.calls, 2)
        self.assertEqual(raised.exception.trace[-1].event, "loop.max_steps_exceeded")

    def test_loop_retries_a_failed_step(self):
        agent = ScriptedAgent(
            [
                RuntimeError("temporary failure"),
                AgentResponse(content="finished", metadata={"done": True}),
            ]
        )
        runner = LoopRunner(
            AgentHarness(agent),
            completion_checker=is_done,
            max_steps=1,
            max_retries=1,
        )

        result = runner.run(AgentRequest(task="retry once"))

        self.assertEqual(result.response.content, "finished")
        self.assertEqual(agent.calls, 2)
        self.assertEqual(
            [event.event for event in result.trace],
            [
                "loop.started",
                "loop.step.started",
                "loop.retry",
                "loop.step.started",
                "loop.step.finished",
                "loop.completed",
            ],
        )

    def test_loop_reports_exhausted_retries(self):
        agent = ScriptedAgent(
            [RuntimeError("failure one"), RuntimeError("failure two")]
        )
        runner = LoopRunner(
            AgentHarness(agent),
            completion_checker=is_done,
            max_steps=1,
            max_retries=1,
        )

        with self.assertRaises(LoopExecutionError) as raised:
            runner.run(AgentRequest(task="fail twice"))

        self.assertEqual(agent.calls, 2)
        self.assertEqual(str(raised.exception.cause.cause), "failure two")
        self.assertEqual(raised.exception.trace[-1].event, "loop.failed")

    def test_loop_rejects_invalid_limits(self):
        with self.assertRaises(ValueError):
            LoopRunner(
                AgentHarness(ScriptedAgent([])),
                completion_checker=is_done,
                max_steps=0,
            )

        with self.assertRaises(ValueError):
            LoopRunner(
                AgentHarness(ScriptedAgent([])),
                completion_checker=is_done,
                max_retries=-1,
            )


if __name__ == "__main__":
    unittest.main()
