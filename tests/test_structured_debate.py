import copy
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    CombinedAnalysisQuery,
    StructuredDebateError,
    StructuredDebateQuery,
    build_default_combined_analysis_runtime,
    build_default_structured_debate_runtime,
    validate_structured_debate,
)


class StructuredDebateTests(unittest.TestCase):
    def setUp(self):
        combined_result = build_default_combined_analysis_runtime(
            project_root=PROJECT_ROOT
        ).run(CombinedAnalysisQuery.for_symbol())
        self.bundle = combined_result.to_mapping()["report"]
        self.runtime = build_default_structured_debate_runtime()

    def test_two_round_debate_keeps_claim_evidence_reasoning_and_balance(self):
        result = self.runtime.run(StructuredDebateQuery(self.bundle)).to_mapping()
        report = result["report"]

        self.assertEqual(report["status"], "debate_completed")
        self.assertEqual(len(report["rounds"]), 2)
        self.assertEqual(
            report["evidence_balance"]["bull_specialists"],
            ["fundamental", "industry"],
        )
        self.assertEqual(
            report["evidence_balance"]["bear_specialists"],
            ["macro", "technical"],
        )
        self.assertFalse(report["evidence_balance"]["single_sided_evidence"])
        for debate_round in report["rounds"]:
            for side in ("bull", "bear"):
                claim = debate_round[side]
                self.assertIn("claim", claim)
                self.assertIn("evidence", claim)
                self.assertIn("reasoning", claim)
                self.assertTrue(claim["evidence"])
        self.assertIn("debate.cross_validation.passed", [
            event["event"] for event in result["trace"]
        ])

    def test_three_round_debate_can_run_as_a_graph_node(self):
        result = self.runtime.run_graph_node(
            {"combined_analysis": self.bundle, "debate_rounds": 3}
        )

        self.assertEqual(
            result["structured_debate"]["report"]["status"],
            "debate_completed",
        )
        self.assertEqual(
            len(result["structured_debate"]["report"]["rounds"]),
            3,
        )
        self.assertEqual(
            result["structured_debate"]["report"]["rounds"][2]["bull"]["counter_to"],
            "bear.r2",
        )

    def test_query_rejects_invalid_round_count(self):
        with self.assertRaises(StructuredDebateError):
            StructuredDebateQuery(self.bundle, rounds=1)
        with self.assertRaises(StructuredDebateError):
            StructuredDebateQuery(self.bundle, rounds=4)

    def test_validator_rejects_tampered_evidence_path(self):
        report = self.runtime.run(StructuredDebateQuery(self.bundle)).to_mapping()["report"]
        tampered = copy.deepcopy(report)
        tampered["rounds"][0]["bull"]["evidence"][0]["path"] = (
            "reports.fundamental.not_a_real_field"
        )

        validation = validate_structured_debate(tampered, self.bundle)

        self.assertFalse(validation.valid)
        self.assertIn("path", validation.detail)


if __name__ == "__main__":
    unittest.main()
