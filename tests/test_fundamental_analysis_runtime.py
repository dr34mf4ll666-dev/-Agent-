import copy
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "fundamental_analysis.json"
SAMPLE_REPORT_PATH = (
    PROJECT_ROOT / "docs" / "examples" / "fundamental-analysis-sz000001.json"
)
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import CognitiveLoopExecutionError, GraphDefinition, GraphRunner
from agent_platform.finance import (
    FundamentalAnalysisError,
    FundamentalAnalysisQuery,
    FundamentalAnalysisRuntime,
    build_default_fundamental_analysis_runtime,
    validate_fundamental_analysis_output,
)


def default_query():
    return FundamentalAnalysisQuery(symbol="sz000001")


class BrokenSourceFinancialTool:
    def run(self, arguments):
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        dataset = arguments["dataset"]
        entry = copy.deepcopy(
            next(item for item in fixture["datasets"] if item["dataset"] == dataset)
        )
        entry["records"][0].pop("source")
        return entry


class FundamentalAnalysisRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = build_default_fundamental_analysis_runtime(
            project_root=PROJECT_ROOT
        )

    def test_offline_runtime_calculates_statements_valuation_and_dcf(self):
        result = self.runtime.run(default_query()).to_mapping()
        report = result["report"]
        analysis = report["analysis"]
        expected_sample = json.loads(
            SAMPLE_REPORT_PATH.read_text(encoding="utf-8")
        )

        self.assertEqual(analysis, expected_sample)
        self.assertEqual(analysis["symbol"], "sz000001")
        self.assertEqual(analysis["annual_base_period"], "2025-12-31T00:00:00+08:00")
        self.assertEqual(analysis["valuation"]["pe_dynamic"], "5.040000")
        self.assertEqual(analysis["valuation"]["pb"], "0.480000")
        self.assertEqual(
            analysis["valuation"]["valuation_percentile_method"],
            "rule_based_not_historical",
        )
        self.assertEqual(analysis["dcf"]["method"], "discounted_earnings_proxy")
        self.assertEqual(analysis["dcf"]["forecast_years"], 5)
        self.assertEqual(len(analysis["score_components"]), 6)
        self.assertEqual(result["loop"]["steps"], 1)
        self.assertEqual(result["loop"]["allowed_tools"], ["fundamental_analysis"])
        harness_details = {
            event["detail"] for event in result["loop"]["harness_trace"]
        }
        self.assertIn("fundamental_output_schema", harness_details)
        self.assertIn("fundamental_market_sources", harness_details)
        self.assertIn("fundamental_value_recompute", harness_details)

    def test_cross_validator_rejects_tampered_score(self):
        report = copy.deepcopy(
            self.runtime.run(default_query()).to_mapping()["report"]
        )
        report["analysis"]["score"] = 99

        validation = validate_fundamental_analysis_output(report)

        self.assertFalse(validation.valid)
        self.assertIn("does not match", validation.detail)

    def test_missing_source_is_blocked_inside_loop(self):
        runtime = FundamentalAnalysisRuntime(BrokenSourceFinancialTool())

        with self.assertRaises(CognitiveLoopExecutionError) as raised:
            runtime.run(default_query())

        self.assertTrue(
            all(not record.observation.success for record in raised.exception.tool_records)
        )
        errors = [
            record.observation.error
            for record in raised.exception.tool_records
            if record.observation.error
        ]
        self.assertTrue(any("source" in error for error in errors))

    def test_same_runtime_is_usable_as_a_graph_node(self):
        graph = GraphDefinition(
            start="fundamental",
            nodes={"fundamental": self.runtime.run_graph_node},
        )

        result = GraphRunner(graph).run(
            {"fundamental_query": default_query().to_mapping()}
        )

        self.assertEqual(result.statuses["fundamental"], "completed")
        self.assertEqual(
            result.state["fundamental_report"]["score_label"], "strong_positive"
        )
        self.assertEqual(result.state["fundamental_loop"]["steps"], 1)
        self.assertIn("balance_sheet", result.state["fundamental_evidence"])

    def test_query_rejects_invalid_mode_limit_and_year(self):
        invalid_values = (
            {"symbol": "sz000001", "mode": "auto"},
            {"symbol": "sz000001", "limit": 0},
            {"symbol": "sz000001", "start_year": "24"},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(FundamentalAnalysisError):
                    FundamentalAnalysisQuery.from_mapping(value)


if __name__ == "__main__":
    unittest.main()
