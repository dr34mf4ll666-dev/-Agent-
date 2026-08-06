import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    ModelErrorCode,
    ModelGateway,
    ModelGatewayConfigurationError,
    ModelGatewayExecutionError,
    ModelRequest,
    OpenAIResponsesAdapter,
)


REPORT_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


def completed_response(text='{"summary":"verified"}'):
    return {
        "id": "resp_test_123",
        "status": "completed",
        "model": "gpt-test-snapshot",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {
            "input_tokens": 11,
            "output_tokens": 5,
            "total_tokens": 16,
        },
    }


class RecordingTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, headers, payload, timeout_seconds):
        self.calls.append((url, headers, payload, timeout_seconds))
        return self.response


class OpenAIResponsesAdapterTests(unittest.TestCase):
    def test_missing_environment_key_has_stable_error(self):
        with self.assertRaisesRegex(
            ModelGatewayConfigurationError,
            "OPENAI_API_KEY is required",
        ):
            OpenAIResponsesAdapter.from_env(model="gpt-test", env={})

    def test_structured_request_and_response_are_translated(self):
        transport = RecordingTransport(completed_response())
        adapter = OpenAIResponsesAdapter(
            model="gpt-test",
            env={"OPENAI_API_KEY": "secret-for-test"},
            transport=transport,
        )

        result = ModelGateway(adapter).generate(
            ModelRequest(
                prompt="summarize",
                system_prompt="Return evidence only.",
                response_schema=REPORT_SCHEMA,
                schema_name="report",
                max_output_tokens=80,
            )
        )

        url, headers, payload, timeout = transport.calls[0]
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer secret-for-test")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["input"][0]["role"], "system")
        self.assertEqual(payload["input"][1]["role"], "user")
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(payload["text"]["format"]["schema"], REPORT_SCHEMA)
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertFalse(payload["store"])
        self.assertEqual(timeout, 30.0)
        self.assertEqual(result.response.structured_output, {"summary": "verified"})
        self.assertEqual(result.response.model, "gpt-test-snapshot")
        self.assertEqual(result.response.usage.total_tokens, 16)
        self.assertEqual(result.response.response_id, "resp_test_123")

    def test_plain_request_does_not_send_structured_format(self):
        transport = RecordingTransport(completed_response("plain answer"))
        adapter = OpenAIResponsesAdapter(
            model="gpt-test",
            env={"OPENAI_API_KEY": "secret-for-test"},
            transport=transport,
        )

        result = ModelGateway(adapter).generate(ModelRequest(prompt="hello"))

        self.assertNotIn("text", transport.calls[0][2])
        self.assertEqual(result.response.content, "plain answer")

    def test_refusal_is_normalized_without_retry(self):
        response = completed_response()
        response["output"][0]["content"] = [
            {"type": "refusal", "refusal": "cannot comply"}
        ]
        adapter = OpenAIResponsesAdapter(
            model="gpt-test",
            env={"OPENAI_API_KEY": "secret-for-test"},
            transport=RecordingTransport(response),
        )

        with self.assertRaises(ModelGatewayExecutionError) as raised:
            ModelGateway(adapter).generate(ModelRequest(prompt="hello"))

        self.assertEqual(raised.exception.code, ModelErrorCode.REFUSAL)
        self.assertFalse(raised.exception.retriable)

    def test_missing_usage_is_normalized_as_invalid_response(self):
        response = completed_response()
        del response["usage"]
        adapter = OpenAIResponsesAdapter(
            model="gpt-test",
            env={"OPENAI_API_KEY": "secret-for-test"},
            transport=RecordingTransport(response),
        )

        with self.assertRaises(ModelGatewayExecutionError) as raised:
            ModelGateway(adapter).generate(ModelRequest(prompt="hello"))

        self.assertEqual(raised.exception.code, ModelErrorCode.INVALID_RESPONSE)

    def test_http_error_mapping_separates_retryable_rate_limit_and_quota(self):
        rate_error = OpenAIResponsesAdapter._map_http_error(
            429,
            {"error": {"message": "slow down", "code": "rate_limit_exceeded"}},
        )
        quota_error = OpenAIResponsesAdapter._map_http_error(
            429,
            {"error": {"message": "no credit", "code": "credit_balance_exhausted"}},
        )
        server_error = OpenAIResponsesAdapter._map_http_error(503, {})

        self.assertEqual(rate_error.code, ModelErrorCode.RATE_LIMIT)
        self.assertTrue(rate_error.retriable)
        self.assertEqual(quota_error.code, ModelErrorCode.QUOTA)
        self.assertFalse(quota_error.retriable)
        self.assertEqual(server_error.code, ModelErrorCode.SERVICE_UNAVAILABLE)
        self.assertTrue(server_error.retriable)


if __name__ == "__main__":
    unittest.main()
