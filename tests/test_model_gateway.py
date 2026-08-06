import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    MockModelAdapter,
    ModelAdapterResponse,
    ModelErrorCode,
    ModelGateway,
    ModelGatewayConfigurationError,
    ModelGatewayExecutionError,
    ModelProviderError,
    ModelRequest,
    ModelRetryPolicy,
    ModelUsage,
)


REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": ["summary", "score"],
    "additionalProperties": False,
}


class SequenceClock:
    def __init__(self, *values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


class ModelGatewayTests(unittest.TestCase):
    def test_mock_returns_usage_latency_attempts_and_trace(self):
        adapter = MockModelAdapter(
            content="offline answer",
            usage=ModelUsage(input_tokens=4, output_tokens=2, total_tokens=6),
        )
        gateway = ModelGateway(adapter, clock=SequenceClock(10.0, 10.125))

        result = gateway.generate(ModelRequest(prompt="hello"))

        self.assertEqual(result.response.content, "offline answer")
        self.assertEqual(result.response.provider, "mock")
        self.assertEqual(result.response.model, "mock-deterministic-v1")
        self.assertEqual(result.response.usage.total_tokens, 6)
        self.assertEqual(result.response.latency_ms, 125)
        self.assertEqual(result.response.attempts, 1)
        self.assertEqual(result.response.status, "succeeded")
        self.assertEqual(
            [event.event for event in result.trace],
            [
                "gateway.started",
                "model.attempt.started",
                "model.attempt.succeeded",
                "gateway.succeeded",
            ],
        )
        self.assertIn("input_tokens=4", result.trace[-1].detail)

    def test_structured_output_is_validated_locally(self):
        adapter = MockModelAdapter(
            content='{"summary":"safe","score":80}',
            structured_output={"summary": "safe", "score": 80},
        )

        result = ModelGateway(adapter).generate(
            ModelRequest(
                prompt="build report",
                response_schema=REPORT_SCHEMA,
                schema_name="report",
            )
        )

        self.assertEqual(result.response.structured_output["score"], 80)

    def test_invalid_structured_output_is_a_non_retriable_failure(self):
        adapter = MockModelAdapter(
            content='{"summary":"safe","score":"80"}',
            structured_output={"summary": "safe", "score": "80"},
        )

        with self.assertRaises(ModelGatewayExecutionError) as raised:
            ModelGateway(adapter).generate(
                ModelRequest(prompt="build report", response_schema=REPORT_SCHEMA)
            )

        self.assertEqual(raised.exception.code, ModelErrorCode.INVALID_RESPONSE)
        self.assertFalse(raised.exception.retriable)
        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(adapter.calls, 1)

    def test_retriable_failure_uses_finite_backoff_then_succeeds(self):
        adapter = MockModelAdapter(
            script=[
                ModelProviderError(
                    "busy",
                    code=ModelErrorCode.SERVICE_UNAVAILABLE,
                    retriable=True,
                ),
                ModelAdapterResponse(
                    content="recovered",
                    model="mock-deterministic-v1",
                    usage=ModelUsage(3, 1, 4),
                ),
            ]
        )
        sleeps = []
        gateway = ModelGateway(
            adapter,
            retry_policy=ModelRetryPolicy(
                max_attempts=2,
                timeout_seconds=7,
                initial_backoff_seconds=0.5,
            ),
            clock=SequenceClock(1.0, 1.2),
            sleeper=sleeps.append,
        )

        result = gateway.generate(ModelRequest(prompt="retry"))

        self.assertEqual(result.response.content, "recovered")
        self.assertEqual(result.response.attempts, 2)
        self.assertEqual(adapter.timeouts, [7.0, 7.0])
        self.assertEqual(sleeps, [0.5])
        self.assertIn("model.retry.scheduled", [event.event for event in result.trace])

    def test_non_retriable_failure_stops_after_one_attempt(self):
        adapter = MockModelAdapter(
            script=[
                ModelProviderError(
                    "bad key",
                    code=ModelErrorCode.AUTHENTICATION,
                    retriable=False,
                )
            ]
        )

        with self.assertRaises(ModelGatewayExecutionError) as raised:
            ModelGateway(adapter).generate(ModelRequest(prompt="hello"))

        self.assertEqual(raised.exception.code, ModelErrorCode.AUTHENTICATION)
        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(raised.exception.trace[-1].event, "gateway.failed")

    def test_timeout_is_normalized_and_retried_only_to_limit(self):
        adapter = MockModelAdapter(script=[TimeoutError(), TimeoutError()])
        gateway = ModelGateway(
            adapter,
            retry_policy=ModelRetryPolicy(
                max_attempts=2,
                timeout_seconds=1,
                initial_backoff_seconds=0,
            ),
            sleeper=lambda _: None,
        )

        with self.assertRaises(ModelGatewayExecutionError) as raised:
            gateway.generate(ModelRequest(prompt="hello"))

        self.assertEqual(raised.exception.code, ModelErrorCode.TIMEOUT)
        self.assertTrue(raised.exception.retriable)
        self.assertEqual(raised.exception.attempts, 2)
        self.assertEqual(adapter.calls, 2)

    def test_unsupported_schema_is_rejected_before_provider_call(self):
        adapter = MockModelAdapter(content="unused")
        request = ModelRequest(
            prompt="hello",
            response_schema={"oneOf": [{"type": "string"}]},
        )

        with self.assertRaises(ModelGatewayConfigurationError):
            ModelGateway(adapter).generate(request)

        self.assertEqual(adapter.calls, 0)

    def test_request_and_retry_policy_reject_invalid_configuration(self):
        with self.assertRaises(ModelGatewayConfigurationError):
            ModelRequest(prompt=" ")
        with self.assertRaises(ModelGatewayConfigurationError):
            ModelRetryPolicy(max_attempts=0)


if __name__ == "__main__":
    unittest.main()
