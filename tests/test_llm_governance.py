import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (  # noqa: E402
    MockModelAdapter,
    ModelGateway,
    ModelRequest,
    ModelUsage,
)
from agent_platform.llm_governance import (  # noqa: E402
    GovernanceBudgetExceeded,
    GovernancePolicy,
    ModelGovernanceRuntime,
)


class LLMGovernanceTests(unittest.TestCase):
    def test_versioned_gateway_reports_metadata_and_reuses_success_cache(self):
        adapter = MockModelAdapter(
            content="safe answer",
            usage=ModelUsage(input_tokens=4, output_tokens=2, total_tokens=6),
        )
        runtime = ModelGovernanceRuntime(
            ModelGateway(adapter),
            policy=GovernancePolicy(
                policy_version="p7-policy-test",
                prompt_version="market-prompt-test",
                schema_version="market-schema-test",
                route="deepseek",
                max_calls=2,
                max_total_tokens=100,
                max_output_tokens=20,
                cache_ttl_seconds=60,
            ),
        )
        request = ModelRequest(
            prompt="same prompt",
            system_prompt="safe system",
            schema_name="test_output",
            max_output_tokens=10,
        )

        first = runtime.generate(request, operation="client_explanation")
        second = runtime.generate(request, operation="client_explanation")

        self.assertEqual(adapter.calls, 1)
        self.assertEqual(first.response.content, "safe answer")
        self.assertFalse(first.governance["cache_hit"])
        self.assertTrue(second.governance["cache_hit"])
        self.assertEqual(second.governance["policy_version"], "p7-policy-test")
        self.assertEqual(second.governance["prompt_version"], "market-prompt-test")
        self.assertEqual(second.governance["schema_version"], "market-schema-test")
        self.assertEqual(second.governance["tokens_used"], 6)
        self.assertFalse(second.governance["degraded"])

    def test_budget_rejects_a_new_model_call_before_provider_execution(self):
        adapter = MockModelAdapter(
            content="safe answer",
            usage=ModelUsage(input_tokens=3, output_tokens=1, total_tokens=4),
        )
        runtime = ModelGovernanceRuntime(
            ModelGateway(adapter),
            policy=GovernancePolicy(
                max_calls=1,
                max_total_tokens=100,
                max_output_tokens=20,
            ),
        )
        runtime.generate(ModelRequest(prompt="first", max_output_tokens=10))

        with self.assertRaises(GovernanceBudgetExceeded):
            runtime.generate(ModelRequest(prompt="different", max_output_tokens=10))

        self.assertEqual(adapter.calls, 1)

    def test_request_output_limit_is_checked_before_provider_execution(self):
        adapter = MockModelAdapter(content="unused")
        runtime = ModelGovernanceRuntime(
            ModelGateway(adapter),
            policy=GovernancePolicy(max_output_tokens=8),
        )

        with self.assertRaises(GovernanceBudgetExceeded):
            runtime.generate(ModelRequest(prompt="too long", max_output_tokens=9))

        self.assertEqual(adapter.calls, 0)


if __name__ == "__main__":
    unittest.main()
