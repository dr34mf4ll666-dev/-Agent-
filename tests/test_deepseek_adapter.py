import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    DeepSeekChatAdapter,
    ModelErrorCode,
    ModelGateway,
    ModelGatewayConfigurationError,
    ModelGatewayExecutionError,
    ModelRequest,
    ModelRetryPolicy,
)


REPORT_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


def completed_response(text='{"summary":"verified"}'):
    return {
        "id": "chatcmpl_deepseek_test",
        "object": "chat.completion",
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": {
            "prompt_tokens": 13,
            "completion_tokens": 4,
            "total_tokens": 17,
        },
    }


class RecordingTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, headers, payload, timeout_seconds):
        self.calls.append((url, headers, payload, timeout_seconds))
        return self.response


class DeepSeekChatAdapterTests(unittest.TestCase):
    def test_missing_environment_key_has_stable_error(self):
        with self.assertRaisesRegex(
            ModelGatewayConfigurationError,
            "DEEPSEEK_API_KEY is required",
        ):
            DeepSeekChatAdapter.from_env(model="deepseek-v4-flash", env={})

    def test_structured_request_and_response_are_translated(self):
        transport = RecordingTransport(completed_response())
        adapter = DeepSeekChatAdapter(
            model="deepseek-v4-flash",
            env={"DEEPSEEK_API_KEY": "fake-deepseek-key"},
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
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer fake-deepseek-key")
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("JSON Schema", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 80)
        self.assertFalse(payload["stream"])
        self.assertEqual(timeout, 30.0)
        self.assertEqual(result.response.structured_output, {"summary": "verified"})
        self.assertEqual(result.response.model, "deepseek-v4-flash")
        self.assertEqual(result.response.usage.input_tokens, 13)
        self.assertEqual(result.response.usage.output_tokens, 4)
        self.assertEqual(result.response.response_id, "chatcmpl_deepseek_test")

    def test_plain_request_does_not_enable_json_output(self):
        transport = RecordingTransport(completed_response("plain answer"))
        adapter = DeepSeekChatAdapter(
            model="deepseek-v4-flash",
            env={"DEEPSEEK_API_KEY": "fake-deepseek-key"},
            transport=transport,
        )

        result = ModelGateway(adapter).generate(ModelRequest(prompt="hello"))

        self.assertNotIn("response_format", transport.calls[0][2])
        self.assertEqual(result.response.content, "plain answer")

    def test_incomplete_resource_response_is_retriable(self):
        response = completed_response()
        response["choices"][0]["finish_reason"] = "insufficient_system_resource"
        adapter = DeepSeekChatAdapter(
            model="deepseek-v4-flash",
            env={"DEEPSEEK_API_KEY": "fake-deepseek-key"},
            transport=RecordingTransport(response),
        )

        with self.assertRaises(ModelGatewayExecutionError) as raised:
            ModelGateway(
                adapter,
                retry_policy=ModelRetryPolicy(max_attempts=1),
            ).generate(ModelRequest(prompt="hello"))

        self.assertEqual(raised.exception.code, ModelErrorCode.SERVICE_UNAVAILABLE)
        self.assertTrue(raised.exception.retriable)

    def test_http_error_mapping_matches_deepseek_error_contract(self):
        quota_error = DeepSeekChatAdapter._map_http_error(402, {})
        rate_error = DeepSeekChatAdapter._map_http_error(429, {})
        server_error = DeepSeekChatAdapter._map_http_error(503, {})

        self.assertEqual(quota_error.code, ModelErrorCode.QUOTA)
        self.assertFalse(quota_error.retriable)
        self.assertEqual(rate_error.code, ModelErrorCode.RATE_LIMIT)
        self.assertTrue(rate_error.retriable)
        self.assertEqual(server_error.code, ModelErrorCode.SERVICE_UNAVAILABLE)
        self.assertTrue(server_error.retriable)


if __name__ == "__main__":
    unittest.main()
