import copy
import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    C1DecisionError,
    C1DecisionQuery,
    C1DecisionRuntime,
    CombinedAnalysisQuery,
    build_default_c1_decision_runtime,
    build_default_combined_analysis_runtime,
    build_default_structured_debate_runtime,
    validate_c1_decision,
)


class _MappingResult:
    def __init__(self, value):
        self._value = copy.deepcopy(value)

    def to_mapping(self):
        return copy.deepcopy(self._value)


class _FixedCombinedRuntime:
    def __init__(self, value):
        self._value = copy.deepcopy(value)

    def run(self, query):
        return _MappingResult(self._value)


class _OneSidedDebateRuntime:
    def __init__(self):
        self._delegate = build_default_structured_debate_runtime()

    def run(self, query):
        result = self._delegate.run(query).to_mapping()
        result["report"]["evidence_balance"]["bear_specialists"] = ["macro"]
        return _MappingResult(result)


class C1DecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.query = C1DecisionQuery(CombinedAnalysisQuery.for_symbol())
        cls.runtime = build_default_c1_decision_runtime(project_root=PROJECT_ROOT)
        cls.combined = build_default_combined_analysis_runtime(
            project_root=PROJECT_ROOT
        ).run(cls.query.combined_query).to_mapping()

    def test_complete_c1_outputs_synthesis_targets_confidence_and_gate(self):
        result = self.runtime.run(self.query).to_mapping()
        report = result["report"]
        synthesis = report["synthesis"]
        interval = synthesis["target_price_interval"]

        self.assertEqual(report["status"], "c1_completed")
        self.assertEqual(synthesis["raw_inclination"], "positive")
        self.assertEqual(synthesis["inclination"], "cautious_positive")
        self.assertGreaterEqual(synthesis["confidence"], 0)
        self.assertLessEqual(synthesis["confidence"], 100)
        self.assertLessEqual(
            Decimal(interval["lower"]), Decimal(interval["reference"])
        )
        self.assertLessEqual(
            Decimal(interval["reference"]), Decimal(interval["upper"])
        )
        self.assertEqual(
            synthesis["side_targets"]["bull_target_price_upper"],
            interval["upper"],
        )
        self.assertEqual(
            synthesis["side_targets"]["bear_target_price_lower"],
            interval["lower"],
        )
        self.assertEqual(report["quality"]["status"], "passed")
        self.assertEqual(
            report["market_regime_gate"]["effective_position_cap_percent"],
            "15",
        )
        self.assertFalse(report["market_regime_gate"]["real_trading_allowed"])
        self.assertEqual(report["next_stage"], "trader_and_risk_manager")
        self.assertIn("c1.completed", [event["event"] for event in result["trace"]])

    def test_bearish_regime_reduces_position_cap_to_ten_percent(self):
        combined = copy.deepcopy(self.combined)
        macro = combined["report"]["reports"]["macro"]
        macro["market_regime"]["label"] = "bearish"
        macro["market_regime"]["rule"] = "test bearish regime"
        macro["risk_appetite"]["label"] = "low"
        macro["score"] = -60
        combined["report"]["summary"]["macro"]["market_regime"] = "bearish"
        combined["report"]["summary"]["macro"]["score"] = -60
        runtime = C1DecisionRuntime(
            combined_runtime=_FixedCombinedRuntime(combined),
            debate_runtime=build_default_structured_debate_runtime(),
        )

        report = runtime.run(self.query).to_mapping()["report"]

        self.assertEqual(report["market_regime_gate"]["regime"], "bearish")
        self.assertEqual(
            report["market_regime_gate"]["effective_position_cap_percent"],
            "10",
        )
        self.assertEqual(report["market_regime_gate"]["status"], "reduced")

    def test_consistency_check_rejects_symbol_contradiction(self):
        combined = copy.deepcopy(self.combined)
        combined["report"]["reports"]["fundamental"]["symbol"] = "sz600000"
        runtime = C1DecisionRuntime(
            combined_runtime=_FixedCombinedRuntime(combined),
            debate_runtime=build_default_structured_debate_runtime(),
        )

        with self.assertRaisesRegex(C1DecisionError, "symbol does not match"):
            runtime.run(self.query)

    def test_bias_detector_rejects_one_sided_declared_evidence(self):
        runtime = C1DecisionRuntime(
            combined_runtime=_FixedCombinedRuntime(self.combined),
            debate_runtime=_OneSidedDebateRuntime(),
        )

        with self.assertRaisesRegex(C1DecisionError, "bias detector failed"):
            runtime.run(self.query)

    def test_validator_rejects_tampered_target_interval(self):
        report = self.runtime.run(self.query).to_mapping()["report"]
        tampered = copy.deepcopy(report)
        tampered["synthesis"]["target_price_interval"]["lower"] = "99.00"

        validation = validate_c1_decision(tampered)

        self.assertFalse(validation.valid)
        self.assertIn("ordered", validation.detail)

    def test_validator_rejects_boolean_confidence(self):
        report = self.runtime.run(self.query).to_mapping()["report"]
        tampered = copy.deepcopy(report)
        tampered["synthesis"]["confidence"] = True

        validation = validate_c1_decision(tampered)

        self.assertFalse(validation.valid)
        self.assertIn("confidence", validation.detail)

    def test_complete_runtime_can_be_used_as_a_graph_node(self):
        result = self.runtime.run_graph_node(self.query.to_mapping())

        self.assertEqual(
            result["c1_decision"]["report"]["status"],
            "c1_completed",
        )
        self.assertEqual(
            result["c1_decision"]["report"]["quality"]["status"],
            "passed",
        )

    def test_query_rejects_invalid_rounds_and_position_cap(self):
        with self.assertRaises(C1DecisionError):
            C1DecisionQuery(self.query.combined_query, debate_rounds=1)
        with self.assertRaises(C1DecisionError):
            C1DecisionQuery(
                self.query.combined_query,
                base_position_cap_percent=0,
            )


if __name__ == "__main__":
    unittest.main()
