import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    AgentHarness,
    AgentRequest,
    AgentResponse,
    CrossValidationResult,
    CrossValidator,
    GuardrailConfigurationError,
    GuardrailRegistry,
    GuardrailViolation,
    HarnessExecutionError,
    JSONSchemaValidator,
    KeywordBlocker,
    RateLimiter,
    SourceAttributionFilter,
)


class StaticAgent:
    name = "static"

    def __init__(self, response):
        self.response = response
        self.calls = 0

    def run(self, request):
        self.calls += 1
        return self.response


class MutableClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class PassGuardrail:
    def __init__(self, name="custom_pass"):
        self.name = name

    def check_input(self, request):
        return None

    def check_output(self, response):
        return None


def report_response(**overrides):
    report = {
        "symbol": "DEMO",
        "score": 80,
        "source": "synthetic_fixture",
        "timestamp": "2026-08-04T12:00:00+08:00",
        "as_of": "2026-08-04T00:00:00+08:00",
    }
    report.update(overrides)
    return AgentResponse(
        content="DEMO report is ready",
        metadata={"report": report},
    )


REPORT_SCHEMA = {
    "type": "object",
    "required": ["symbol", "score", "source", "timestamp", "as_of"],
    "properties": {
        "symbol": {"type": "string", "minLength": 1},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "source": {"type": "string", "minLength": 1},
        "timestamp": {"type": "string", "minLength": 1},
        "as_of": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


class BuiltInGuardrailTests(unittest.TestCase):
    def test_json_schema_validator_accepts_a_matching_output(self):
        harness = AgentHarness(
            StaticAgent(report_response()),
            guardrails=[
                JSONSchemaValidator(
                    output_schema=REPORT_SCHEMA,
                    output_path="metadata.report",
                )
            ],
        )

        result = harness.run(AgentRequest(task="build report"))

        self.assertEqual(result.response.metadata["report"]["score"], 80)
        self.assertIn(
            "guardrail.output.passed",
            [event.event for event in result.trace],
        )

    def test_json_schema_validator_rejects_missing_or_wrong_fields(self):
        invalid_responses = [
            report_response(score="80"),
            AgentResponse(
                content="report",
                metadata={"report": {"symbol": "DEMO"}},
            ),
        ]

        for response in invalid_responses:
            with self.subTest(response=response):
                with self.assertRaises(HarnessExecutionError) as raised:
                    AgentHarness(
                        StaticAgent(response),
                        guardrails=[
                            JSONSchemaValidator(
                                output_schema=REPORT_SCHEMA,
                                output_path="metadata.report",
                            )
                        ],
                    ).run(AgentRequest(task="build report"))

                self.assertIsInstance(raised.exception.cause, GuardrailViolation)
                self.assertEqual(
                    raised.exception.trace[-2].event,
                    "guardrail.output.failed",
                )
                self.assertEqual(
                    raised.exception.trace[-1].event,
                    "postflight.failed",
                )

    def test_json_schema_validator_can_block_invalid_input_before_agent(self):
        agent = StaticAgent(report_response())
        guardrail = JSONSchemaValidator(
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {"symbol": {"type": "string"}},
                "additionalProperties": False,
            },
            input_path="context",
        )

        with self.assertRaises(HarnessExecutionError) as raised:
            AgentHarness(agent, [guardrail]).run(
                AgentRequest(task="build report", context={"symbol": 123})
            )

        self.assertEqual(agent.calls, 0)
        self.assertEqual(raised.exception.trace[-2].event, "guardrail.input.failed")

    def test_source_attribution_filter_accepts_complete_records(self):
        harness = AgentHarness(
            StaticAgent(report_response()),
            guardrails=[
                SourceAttributionFilter(
                    required_fields=("source", "timestamp", "as_of"),
                    output_paths=("metadata.report",),
                )
            ],
        )

        result = harness.run(AgentRequest(task="build report"))

        self.assertEqual(
            result.response.metadata["report"]["source"],
            "synthetic_fixture",
        )

    def test_source_attribution_filter_rejects_missing_provenance(self):
        response = report_response(source="")

        with self.assertRaises(HarnessExecutionError) as raised:
            AgentHarness(
                StaticAgent(response),
                guardrails=[
                    SourceAttributionFilter(
                        required_fields=("source", "timestamp", "as_of"),
                        output_paths=("metadata.report",),
                    )
                ],
            ).run(AgentRequest(task="build report"))

        self.assertIn("missing provenance: source", str(raised.exception.cause))

    def test_rate_limiter_blocks_calls_until_the_window_expires(self):
        clock = MutableClock()
        harness = AgentHarness(
            StaticAgent(report_response()),
            guardrails=[RateLimiter(max_calls=2, period_seconds=60, clock=clock)],
        )

        harness.run(AgentRequest(task="first"))
        clock.value = 1
        harness.run(AgentRequest(task="second"))
        clock.value = 2
        with self.assertRaises(HarnessExecutionError) as raised:
            harness.run(AgentRequest(task="third"))

        self.assertIn("exceeded 2 calls", str(raised.exception.cause))
        self.assertEqual(raised.exception.trace[-2].event, "guardrail.input.failed")

        clock.value = 60
        harness.run(AgentRequest(task="after expiry"))

    def test_keyword_blocker_rejects_input_before_agent(self):
        agent = StaticAgent(report_response())

        with self.assertRaises(HarnessExecutionError) as raised:
            AgentHarness(
                agent,
                guardrails=[KeywordBlocker(["100%收益"])],
            ).run(AgentRequest(task="给我一个100%收益方案"))

        self.assertEqual(agent.calls, 0)
        self.assertIn("blocked keyword: 100%收益", str(raised.exception.cause))

    def test_keyword_blocker_rejects_output_and_preserves_trace(self):
        response = AgentResponse(
            content="这个方案绝对稳赚",
            metadata={"report": {}},
        )

        with self.assertRaises(HarnessExecutionError) as raised:
            AgentHarness(
                StaticAgent(response),
                guardrails=[KeywordBlocker(["绝对稳赚"])],
            ).run(AgentRequest(task="build report"))

        events = [event.event for event in raised.exception.trace]
        self.assertIn("guardrail.output.failed", events)
        self.assertEqual(events[-1], "postflight.failed")

    def test_cross_validator_accepts_and_rejects_deterministic_results(self):
        valid = CrossValidator(
            lambda report: CrossValidationResult(
                valid=report["score"] == 80,
                detail="score mismatch",
            ),
            output_path="metadata.report",
        )
        AgentHarness(StaticAgent(report_response()), [valid]).run(
            AgentRequest(task="build report")
        )

        invalid = CrossValidator(
            lambda report: CrossValidationResult(
                valid=report["score"] == 50,
                detail="score mismatch",
            ),
            output_path="metadata.report",
        )
        with self.assertRaises(HarnessExecutionError) as raised:
            AgentHarness(StaticAgent(report_response()), [invalid]).run(
                AgentRequest(task="build report")
            )

        self.assertIn("score mismatch", str(raised.exception.cause))

    def test_each_guardrail_rejects_invalid_configuration(self):
        invalid_factories = {
            "json_schema": lambda: JSONSchemaValidator(),
            "source": lambda: SourceAttributionFilter(
                input_paths=(),
                output_paths=(),
            ),
            "rate": lambda: RateLimiter(max_calls=0),
            "keyword": lambda: KeywordBlocker([]),
            "cross": lambda: CrossValidator(None),
            "unsupported_schema": lambda: JSONSchemaValidator(
                output_schema={"oneOf": [{"type": "string"}]}
            ),
        }

        for name, factory in invalid_factories.items():
            with self.subTest(name=name):
                with self.assertRaises(GuardrailConfigurationError):
                    factory()

    def test_harness_rejects_duplicate_guardrail_names(self):
        with self.assertRaises(GuardrailConfigurationError):
            AgentHarness(
                StaticAgent(report_response()),
                [KeywordBlocker(["a"]), KeywordBlocker(["b"])],
            )


class GuardrailRegistryTests(unittest.TestCase):
    def test_registry_builds_all_five_builtin_guardrails(self):
        clock = MutableClock()
        registry = GuardrailRegistry.with_builtins(
            clock=clock,
            cross_validators={
                "score_matches": lambda report: report["score"] == 80
            },
        )
        guardrails = registry.build(
            [
                {
                    "type": "json_schema",
                    "output_schema": REPORT_SCHEMA,
                    "output_path": "metadata.report",
                },
                {
                    "type": "source_attribution",
                    "required_fields": ["source", "timestamp", "as_of"],
                    "output_paths": ["metadata.report"],
                },
                {"type": "rate_limiter", "max_calls": 3},
                {
                    "type": "keyword_blocker",
                    "blocked_keywords": ["绝对稳赚"],
                },
                {
                    "type": "cross_validator",
                    "validator": "score_matches",
                    "output_path": "metadata.report",
                },
            ]
        )

        result = AgentHarness(StaticAgent(report_response()), guardrails).run(
            AgentRequest(task="build report")
        )

        self.assertEqual(len(guardrails), 5)
        self.assertEqual(
            sum(event.event == "guardrail.output.passed" for event in result.trace),
            5,
        )

    def test_registry_supports_custom_plugins(self):
        registry = GuardrailRegistry()
        registry.register(
            "custom_pass",
            lambda config: PassGuardrail(**dict(config)),
        )

        guardrails = registry.build(
            [{"type": "custom_pass", "name": "my_custom_guardrail"}]
        )

        self.assertEqual(guardrails[0].name, "my_custom_guardrail")

    def test_registry_reports_unknown_types_and_validators(self):
        registry = GuardrailRegistry.with_builtins()

        invalid_configs = [
            [{"type": "not_registered"}],
            [{"type": "cross_validator", "validator": "missing"}],
            [{"max_calls": 1}],
        ]
        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaises(GuardrailConfigurationError):
                    registry.build(config)


if __name__ == "__main__":
    unittest.main()
