import unittest
from datetime import datetime, timezone

from agent_platform.core import (
    AgentToolPolicy,
    AgentToolPolicyRegistry,
    IndustrialHarness,
    IndustrialHarnessConfig,
    IndustrialHarnessConfigurationError,
    IndustrialHarnessExecutionError,
    ToolPermissionError,
    ToolRegistry,
)


class MutableClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class CountingTool:
    name = "allowed_tool"

    def __init__(self):
        self.calls = 0

    def run(self, arguments):
        self.calls += 1
        return dict(arguments)


class UnauthorizedTool(CountingTool):
    name = "web_search"


class IndustrialHarnessTests(unittest.TestCase):
    def config(self, threshold=3, reset=60):
        return IndustrialHarnessConfig(
            failure_threshold=threshold,
            reset_timeout_seconds=reset,
            tool_policies=(AgentToolPolicy("agent", ("allowed_tool",)),),
        )

    def test_config_parser_rejects_unknown_keys_and_duplicate_agents(self):
        with self.assertRaisesRegex(IndustrialHarnessConfigurationError, "keys"):
            IndustrialHarnessConfig.from_mapping(
                {
                    "version": 1,
                    "circuit_breaker": {
                        "failure_threshold": 3,
                        "reset_timeout_seconds": 60,
                    },
                    "agent_tool_policies": [
                        {"agent": "agent", "allowed_tools": []}
                    ],
                    "unexpected": True,
                }
            )
        with self.assertRaisesRegex(IndustrialHarnessConfigurationError, "duplicate"):
            AgentToolPolicyRegistry(
                (AgentToolPolicy("agent"), AgentToolPolicy("agent"))
            )

    def test_unauthorized_tool_is_denied_before_operation(self):
        harness = IndustrialHarness(self.config())
        calls = 0

        def operation():
            nonlocal calls
            calls += 1

        with self.assertRaises(IndustrialHarnessExecutionError) as caught:
            harness.run(
                agent="agent",
                operation=operation,
                requested_tools=("web_search",),
            )

        self.assertEqual(caught.exception.code, "tool_permission_denied")
        self.assertFalse(caught.exception.operation_executed)
        self.assertEqual(calls, 0)

    def test_three_failures_open_circuit_emit_alert_and_pause_next_run(self):
        clock = MutableClock()
        harness = IndustrialHarness(
            self.config(),
            clock=clock,
            wall_clock=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        calls = 0
        last_error = None

        def fail():
            nonlocal calls
            calls += 1
            raise RuntimeError("boom")

        for _ in range(3):
            try:
                harness.run(agent="agent", operation=fail)
            except IndustrialHarnessExecutionError as error:
                last_error = error

        self.assertEqual(last_error.alerts[0].code, "agent_circuit_opened")
        self.assertEqual(harness.circuit_snapshot("agent")["state"], "open")
        with self.assertRaises(IndustrialHarnessExecutionError) as blocked:
            harness.run(agent="agent", operation=fail)
        self.assertEqual(blocked.exception.code, "circuit_open")
        self.assertFalse(blocked.exception.operation_executed)
        self.assertEqual(calls, 3)

    def test_half_open_success_closes_and_resets_circuit(self):
        clock = MutableClock()
        harness = IndustrialHarness(self.config(threshold=1, reset=10), clock=clock)
        with self.assertRaises(IndustrialHarnessExecutionError):
            harness.run(
                agent="agent",
                operation=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            )
        clock.value += 10

        result = harness.run(agent="agent", operation=lambda: "recovered")

        self.assertEqual(result.value, "recovered")
        self.assertIn("circuit.closed", [event.event for event in result.trace])
        self.assertEqual(harness.circuit_snapshot("agent")["state"], "closed")
        self.assertEqual(
            harness.circuit_snapshot("agent")["consecutive_failures"], 0
        )

    def test_tool_registry_enforces_agent_policy_at_registration_and_dispatch(self):
        tool = CountingTool()
        policies = AgentToolPolicyRegistry(
            (AgentToolPolicy("agent", ("allowed_tool",)),)
        )
        registry = ToolRegistry(
            [tool], agent_name="agent", permission_registry=policies
        )

        self.assertEqual(registry.execute("allowed_tool", {"value": 1}), {"value": 1})
        self.assertEqual(tool.calls, 1)
        with self.assertRaises(ToolPermissionError):
            registry.register(UnauthorizedTool())
        self.assertEqual(registry.names, ("allowed_tool",))
        with self.assertRaises(ToolPermissionError):
            ToolRegistry(
                [tool],
                agent_name="unknown_agent",
                permission_registry=policies,
            )


if __name__ == "__main__":
    unittest.main()
