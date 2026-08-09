import copy
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import GraphDefinition, GraphRunner
from agent_platform.finance import (
    CombinedAnalysisError,
    CombinedAnalysisQuery,
    build_default_combined_analysis_runtime,
    validate_combined_analysis_bundle,
)


class CombinedAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.runtime = build_default_combined_analysis_runtime(project_root=PROJECT_ROOT)
        self.query = CombinedAnalysisQuery.for_symbol()

    def test_planner_runs_all_four_specialists_in_parallel_and_aggregates(self):
        result = self.runtime.run(self.query).to_mapping()
        report = result["report"]

        self.assertEqual(report["status"], "specialists_completed")
        self.assertEqual(
            result["graph"]["execution_order"],
            ["planner", "technical", "fundamental", "industry", "macro", "aggregate"],
        )
        self.assertTrue(
            any(
                event["event"] == "graph.wave.started"
                and "technical,fundamental,industry,macro" in event["detail"]
                for event in result["graph"]["trace"]
            )
        )
        self.assertEqual(
            set(report["summary"]),
            {"technical", "fundamental", "industry", "macro"},
        )
        self.assertEqual(report["summary"]["technical"]["signal_label"], "neutral")
        self.assertEqual(report["summary"]["fundamental"]["score"], 60)
        self.assertEqual(report["summary"]["industry"]["prosperity"], "hot")
        self.assertEqual(report["summary"]["macro"]["market_regime"], "mixed")
        self.assertEqual(report["next_stage"], "bull_bear_debate_and_synthesis")

    def test_combined_runtime_can_be_nested_as_a_graph_node(self):
        graph = GraphDefinition(
            start="combined",
            nodes={"combined": self.runtime.run_graph_node},
        )

        result = GraphRunner(graph).run({"combined_query": self.query.to_mapping()})

        self.assertEqual(result.statuses["combined"], "completed")
        nested = result.state["combined_analysis"]
        self.assertEqual(nested["report"]["status"], "specialists_completed")
        self.assertEqual(nested["graph"]["statuses"]["aggregate"], "completed")

    def test_query_rejects_mismatched_symbol_or_mode(self):
        mismatched_symbol = copy.deepcopy(self.query.to_mapping())
        mismatched_symbol["macro_query"]["symbol"] = "sz600000"
        with self.assertRaises(CombinedAnalysisError):
            CombinedAnalysisQuery.from_mapping(mismatched_symbol)

        mismatched_mode = copy.deepcopy(self.query.to_mapping())
        mismatched_mode["industry_query"]["mode"] = "live"
        with self.assertRaises(CombinedAnalysisError):
            CombinedAnalysisQuery.from_mapping(mismatched_mode)

    def test_bundle_validator_requires_all_specialist_evidence(self):
        result = self.runtime.run(self.query).to_mapping()
        bundle = copy.deepcopy(result["report"])
        del bundle["reports"]["macro"]

        validation = validate_combined_analysis_bundle(bundle)

        self.assertFalse(validation.valid)
        self.assertIn("macro", validation.detail)


if __name__ == "__main__":
    unittest.main()
