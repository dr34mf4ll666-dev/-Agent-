import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.finance import (  # noqa: E402
    CombinedAnalysisQuery,
    DynamicDebateRuntime,
    StructuredDebateQuery,
    build_default_combined_analysis_runtime,
    validate_structured_debate,
)


class _CatalogGateway:
    def __init__(self, *, invalid=False, invented_number=False, blocked_phrase=False):
        self.invalid = invalid
        self.invented_number = invented_number
        self.blocked_phrase = blocked_phrase
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        context = json.loads(request.prompt)
        catalog = context["evidence_catalog"]

        def evidence_ids(*specialists):
            selected = []
            for specialist in specialists:
                selected.append(next(item["id"] for item in catalog if item["specialist"] == specialist))
            return selected

        bull_ids = evidence_ids("fundamental", "industry")
        bear_ids = evidence_ids("technical", "macro")
        if self.invalid:
            bull_ids = ["E999", "E998"]
        rounds = []
        for number in range(1, context["round_count"] + 1):
            bull_claim = "本轮动态看多观点"
            if self.invented_number:
                bull_claim += "，预计上涨 999%"
            if self.blocked_phrase:
                bull_claim += "，保证收益"
            rounds.append(
                {
                    "round": number,
                    "bull": {
                        "claim": bull_claim,
                        "evidence_ids": bull_ids,
                        "reasoning": "只引用基本面和行业证据形成候选观点。",
                    },
                    "bear": {
                        "claim": "本轮动态风险观点",
                        "evidence_ids": bear_ids,
                        "reasoning": "只引用技术和宏观证据说明风险。",
                    },
                }
            )
        output = {"rounds": rounds}
        return SimpleNamespace(
            response=SimpleNamespace(
                structured_output=output,
                provider="deepseek",
                model="deepseek-test",
                usage=SimpleNamespace(input_tokens=100, output_tokens=80, total_tokens=180),
                latency_ms=75,
            )
        )


class DynamicDebateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = build_default_combined_analysis_runtime(
            project_root=PROJECT_ROOT
        ).run(CombinedAnalysisQuery.for_symbol()).to_mapping()["report"]
        cls.query = StructuredDebateQuery(cls.bundle, rounds=2)

    def test_valid_model_candidate_is_rehydrated_and_cross_validated(self):
        gateway = _CatalogGateway()

        result = DynamicDebateRuntime(gateway=gateway, max_semantic_attempts=2).run(
            self.query
        ).to_mapping()

        self.assertEqual(result["mode"], "dynamic")
        self.assertEqual(result["provider"], "deepseek")
        self.assertEqual(result["report"]["rounds"][0]["bull"]["claim"], "本轮动态看多观点")
        self.assertTrue(validate_structured_debate(result["report"], self.bundle).valid)
        self.assertEqual(len(gateway.requests), 1)
        self.assertNotIn("reports", json.loads(gateway.requests[0].prompt))

    def test_unknown_evidence_id_retries_then_falls_back(self):
        gateway = _CatalogGateway(invalid=True)

        result = DynamicDebateRuntime(gateway=gateway, max_semantic_attempts=2).run(
            self.query
        ).to_mapping()

        self.assertEqual(result["mode"], "deterministic_fallback")
        self.assertIn("E999", result["fallback_reason"])
        self.assertEqual(len(gateway.requests), 2)
        self.assertTrue(validate_structured_debate(result["report"], self.bundle).valid)

    def test_missing_gateway_uses_existing_deterministic_debate(self):
        result = DynamicDebateRuntime(gateway=None).run(self.query).to_mapping()

        self.assertEqual(result["mode"], "deterministic_fallback")
        self.assertEqual(result["provider"], "local")
        self.assertIn("未配置", result["fallback_reason"])
        self.assertTrue(validate_structured_debate(result["report"], self.bundle).valid)

    def test_invented_number_is_rejected_and_falls_back(self):
        result = DynamicDebateRuntime(
            gateway=_CatalogGateway(invented_number=True), max_semantic_attempts=1
        ).run(self.query).to_mapping()

        self.assertEqual(result["mode"], "deterministic_fallback")
        self.assertIn("unsupported numbers", result["fallback_reason"])

    def test_guaranteed_return_language_is_rejected_and_falls_back(self):
        result = DynamicDebateRuntime(
            gateway=_CatalogGateway(blocked_phrase=True), max_semantic_attempts=1
        ).run(self.query).to_mapping()

        self.assertEqual(result["mode"], "deterministic_fallback")
        self.assertIn("blocked phrase", result["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
