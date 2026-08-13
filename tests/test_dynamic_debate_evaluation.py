import sys
import json
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.finance import DynamicDebateEvaluationRuntime  # noqa: E402


class _AlwaysInvalidGateway:
    def generate(self, request):
        context = json.loads(request.prompt)
        rounds = [
            {
                "round": number,
                "bull": {
                    "claim": "无效多方候选",
                    "evidence_ids": ["E999", "E998"],
                    "reasoning": "引用不存在证据。",
                },
                "bear": {
                    "claim": "无效空方候选",
                    "evidence_ids": ["E997", "E996"],
                    "reasoning": "引用不存在证据。",
                },
            }
            for number in range(1, context["round_count"] + 1)
        ]
        return SimpleNamespace(
            response=SimpleNamespace(
                structured_output={"rounds": rounds},
                provider="scripted_invalid",
                model="always-invalid",
                usage=SimpleNamespace(input_tokens=10, output_tokens=10, total_tokens=20),
                latency_ms=5,
            )
        )


class DynamicDebateEvaluationTests(unittest.TestCase):
    def test_fixed_offline_evaluation_returns_baseline_dynamic_metrics_and_raw_runs(self):
        report = DynamicDebateEvaluationRuntime.from_project(
            PROJECT_ROOT,
            live=False,
        ).run()
        value = report.to_mapping()

        self.assertEqual(value["dataset"]["name"], "dynamic-debate-fixed-v1")
        self.assertEqual(value["dataset"]["run_count"], 4)
        self.assertEqual(len(value["raw_results"]), 4)
        self.assertEqual(value["baseline"]["evidence_validity_rate_percent"], 100.0)
        self.assertEqual(value["baseline"]["bull_bear_balance_rate_percent"], 100.0)
        self.assertEqual(value["baseline"]["retry_rate_percent"], 0.0)
        self.assertEqual(value["baseline"]["total_tokens"], 0)
        self.assertEqual(value["dynamic"]["candidate_evidence_validity_rate_percent"], 80.0)
        self.assertEqual(value["dynamic"]["final_evidence_validity_rate_percent"], 100.0)
        self.assertEqual(value["dynamic"]["bull_bear_balance_rate_percent"], 100.0)
        self.assertEqual(value["dynamic"]["retry_rate_percent"], 25.0)
        self.assertEqual(value["dynamic"]["fallback_rate_percent"], 0.0)
        self.assertGreater(value["dynamic"]["viewpoint_diversity_rate_percent"], value["baseline"]["viewpoint_diversity_rate_percent"])
        self.assertGreater(value["dynamic"]["total_tokens"], 0)
        self.assertEqual(value["dynamic"]["result_stability_rate_percent"], 100.0)
        self.assertTrue(value["passed"])
        self.assertTrue(all(value["acceptance"].values()))
        self.assertIn("report", value["raw_results"][0]["dynamic"])
        self.assertIn("trace", value["raw_results"][0]["dynamic"])
        self.assertFalse(value["safety"]["real_trading_allowed"])

    def test_invalid_dataset_is_rejected_before_any_evaluation(self):
        runtime = DynamicDebateEvaluationRuntime.from_project(PROJECT_ROOT, live=False)

        with self.assertRaisesRegex(ValueError, "repetitions"):
            runtime.run(
                {
                    "version": 1,
                    "name": "broken",
                    "model_config": {},
                    "acceptance_thresholds": {
                        "minimum_candidate_evidence_validity_rate_percent": 75,
                        "minimum_final_evidence_validity_rate_percent": 100,
                        "minimum_bull_bear_balance_rate_percent": 100,
                        "maximum_retry_rate_percent": 50,
                        "maximum_fallback_rate_percent": 25,
                        "minimum_result_stability_rate_percent": 100,
                        "dynamic_diversity_must_exceed_baseline": True,
                    },
                    "cases": [{"id": "bad", "rounds": 2, "repetitions": 0}],
                }
            )

    def test_safe_fallback_does_not_count_as_dynamic_model_quality_pass(self):
        report = DynamicDebateEvaluationRuntime(
            project_root=PROJECT_ROOT,
            gateway=_AlwaysInvalidGateway(),
            provider="scripted_invalid",
            model="always-invalid",
            live=False,
        ).run()
        value = report.to_mapping()

        self.assertEqual(value["dynamic"]["fallback_rate_percent"], 100.0)
        self.assertEqual(value["dynamic"]["final_evidence_validity_rate_percent"], 100.0)
        self.assertFalse(value["acceptance"]["降级率不超过阈值"])
        self.assertFalse(value["passed"])


if __name__ == "__main__":
    unittest.main()
