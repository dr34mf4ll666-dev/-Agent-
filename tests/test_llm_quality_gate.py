import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.llm_quality_gate import (  # noqa: E402
    LLMQualityGateRuntime,
    ModelRelease,
    ModelReleaseRegistry,
    QualityGatePolicy,
)


def _report(*, live: bool) -> dict:
    return {
        "provider": "deepseek" if live else "scripted_mock",
        "model": "deepseek-test" if live else "dynamic-debate-eval-v1",
        "live": live,
        "passed": True,
        "raw_results": [
            {
                "dynamic": {
                    "final_evidence_valid": True,
                    "bull_bear_balanced": True,
                    "safety_valid": True,
                }
            }
        ],
        "acceptance": {"所有运行保持交易安全边界": True},
    }


class LLMQualityGateTests(unittest.TestCase):
    def test_mock_report_cannot_pass_a_live_required_gate(self):
        result = LLMQualityGateRuntime().evaluate(
            _report(live=False),
            policy=QualityGatePolicy(require_live=True),
        )

        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["real_model_run"])
        self.assertFalse(result["can_promote"])

    def test_live_report_can_promote_and_then_roll_back_release(self):
        gate = LLMQualityGateRuntime().evaluate(
            _report(live=True),
            policy=QualityGatePolicy(require_live=True),
        )
        registry = ModelReleaseRegistry(
            ModelRelease("prompt-v1", "schema-v1", "deepseek-old")
        )
        candidate = ModelRelease("prompt-v2", "schema-v2", "deepseek-new")

        promoted = registry.promote(candidate, gate)
        rolled_back = registry.rollback()

        self.assertTrue(gate["passed"])
        self.assertTrue(promoted["promoted"])
        self.assertEqual(promoted["active"]["prompt_version"], "prompt-v2")
        self.assertTrue(rolled_back["rolled_back"])
        self.assertEqual(rolled_back["active"]["prompt_version"], "prompt-v1")


if __name__ == "__main__":
    unittest.main()
