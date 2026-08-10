import copy
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    C1DecisionQuery,
    CombinedAnalysisQuery,
    TraderError,
    TraderQuery,
    build_default_c1_decision_runtime,
    build_default_trader_runtime,
    validate_trader_candidate,
)


class TraderRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c1_result = build_default_c1_decision_runtime(
            project_root=PROJECT_ROOT
        ).run(
            C1DecisionQuery(CombinedAnalysisQuery.for_symbol())
        ).to_mapping()
        cls.c1_report = cls.c1_result["report"]
        cls.runtime = build_default_trader_runtime()

    def test_positive_c1_becomes_safe_simulation_buy_candidate(self):
        result = self.runtime.run(TraderQuery(self.c1_result)).to_mapping()
        report = result["report"]

        self.assertEqual(report["status"], "candidate_signal_created")
        self.assertEqual(report["signal"]["action"], "buy")
        self.assertEqual(report["confidence"], 69)
        self.assertEqual(
            report["target_price_interval"],
            self.c1_report["synthesis"]["target_price_interval"],
        )
        self.assertEqual(report["market_context"]["position_cap_percent"], "15")
        self.assertEqual(
            set(report["provenance"]["timestamp"]),
            {"technical", "fundamental", "industry", "macro"},
        )
        self.assertEqual(
            set(report["provenance"]["as_of"]),
            {"technical", "fundamental", "industry", "macro"},
        )
        self.assertTrue(report["execution"]["simulation_only"])
        self.assertFalse(report["execution"]["order_created"])
        self.assertFalse(report["execution"]["real_trading_allowed"])
        self.assertTrue(report["execution"]["requires_risk_review"])
        self.assertTrue(report["execution"]["human_confirmation_required"])
        self.assertEqual(report["execution"]["status"], "awaiting_risk_review")
        self.assertEqual(report["next_stage"], "risk_manager")
        self.assertIn("trader.completed", [item["event"] for item in result["trace"]])
        self.assertIn(
            "guardrail.output.passed",
            [item["event"] for item in result["harness_trace"]],
        )

    def test_weak_c1_becomes_hold_without_human_confirmation(self):
        c1 = copy.deepcopy(self.c1_report)
        c1["synthesis"]["inclination"] = "neutral"
        c1["synthesis"]["raw_inclination"] = "neutral"
        c1["synthesis"]["weighted_score"] = "10.00"
        c1["synthesis"]["confidence"] = 55

        report = self.runtime.run(TraderQuery(c1)).to_mapping()["report"]

        self.assertEqual(report["signal"]["action"], "hold")
        self.assertEqual(report["execution"]["status"], "no_action")
        self.assertFalse(report["execution"]["human_confirmation_required"])
        self.assertFalse(report["execution"]["order_created"])

    def test_candidate_validator_rejects_tampered_action(self):
        report = self.runtime.run(TraderQuery(self.c1_report)).to_mapping()["report"]
        tampered = copy.deepcopy(report)
        tampered["signal"]["action"] = "sell"

        validation = validate_trader_candidate(tampered, self.c1_report)

        self.assertFalse(validation.valid)
        self.assertIn("signal", validation.detail)

    def test_invalid_c1_is_rejected_before_trader_runs(self):
        tampered = copy.deepcopy(self.c1_report)
        tampered["synthesis"]["target_price_interval"]["lower"] = "999.00"

        with self.assertRaisesRegex(TraderError, "invalid C1 decision"):
            TraderQuery(tampered)

    def test_runtime_can_be_used_as_a_graph_node(self):
        result = self.runtime.run_graph_node({"c1_decision": self.c1_result})

        report = result["trader_candidate"]["report"]
        self.assertEqual(report["signal"]["action"], "buy")
        self.assertFalse(report["execution"]["order_created"])


if __name__ == "__main__":
    unittest.main()
