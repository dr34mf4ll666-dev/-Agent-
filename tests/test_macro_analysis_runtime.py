import copy
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "macro_analysis.json"
SAMPLE_REPORT_PATH = PROJECT_ROOT / "docs" / "examples" / "macro-analysis-sh000300.json"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import CognitiveLoopExecutionError, GraphDefinition, GraphRunner
from agent_platform.finance import (
    FinancialDataError,
    FinancialDataErrorCode,
    MacroAnalysisError,
    MacroAnalysisQuery,
    MacroAnalysisRuntime,
    build_default_macro_analysis_runtime,
    validate_macro_analysis_output,
)
from agent_platform.finance.macro_runtime import DERIVED_EMPTY_RESEARCH_SOURCE


def default_query():
    return MacroAnalysisQuery(index_symbol="sh000300", symbol="sz000001")


class BrokenSourceFinancialTool:
    def run(self, arguments):
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        dataset = arguments["dataset"]
        entry = copy.deepcopy(
            next(item for item in fixture["datasets"] if item["dataset"] == dataset)
        )
        entry["records"][0].pop("source")
        return entry


class EmptyResearchFinancialTool:
    def run(self, arguments):
        if arguments["dataset"] == "sentiment.research":
            raise FinancialDataError(
                "provider returned no records",
                code=FinancialDataErrorCode.EMPTY_RESPONSE,
                source="akshare.stock_research_report_em",
            )
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        return copy.deepcopy(
            next(
                item
                for item in fixture["datasets"]
                if item["dataset"] == arguments["dataset"]
            )
        )


class MacroAnalysisRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = build_default_macro_analysis_runtime(project_root=PROJECT_ROOT)

    def test_offline_runtime_calculates_regime_funds_sentiment_and_risk(self):
        result = self.runtime.run(default_query()).to_mapping()
        report = result["report"]
        analysis = report["analysis"]
        expected_sample = json.loads(SAMPLE_REPORT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(analysis, expected_sample)
        self.assertEqual(analysis["index"]["trend"], "bullish")
        self.assertEqual(analysis["funds"]["direction"], "outflow")
        self.assertEqual(analysis["sentiment"]["label"], "neutral")
        self.assertEqual(analysis["market_regime"]["label"], "mixed")
        self.assertEqual(analysis["risk_appetite"]["label"], "low")
        self.assertEqual(result["loop"]["steps"], 1)
        self.assertEqual(result["loop"]["allowed_tools"], ["macro_analysis"])
        harness_details = {
            event["detail"] for event in result["loop"]["harness_trace"]
        }
        self.assertIn("macro_output_schema", harness_details)
        self.assertIn("macro_market_sources", harness_details)
        self.assertIn("macro_value_recompute", harness_details)

    def test_cross_validator_rejects_tampered_regime(self):
        report = copy.deepcopy(self.runtime.run(default_query()).to_mapping()["report"])
        report["analysis"]["market_regime"]["label"] = "risk_on"

        validation = validate_macro_analysis_output(report)

        self.assertFalse(validation.valid)
        self.assertIn("does not match", validation.detail)

    def test_missing_source_is_blocked_inside_loop(self):
        runtime = MacroAnalysisRuntime(BrokenSourceFinancialTool())

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
        graph = GraphDefinition(start="macro", nodes={"macro": self.runtime.run_graph_node})

        result = GraphRunner(graph).run({"macro_query": default_query().to_mapping()})

        self.assertEqual(result.statuses["macro"], "completed")
        self.assertEqual(result.state["macro_report"]["market_regime"]["label"], "mixed")
        self.assertEqual(result.state["macro_loop"]["steps"], 1)
        self.assertIn("macro_index", result.state["macro_evidence"])

    def test_query_rejects_invalid_symbols_dates_and_limit(self):
        invalid_values = (
            {"index_symbol": "300", "symbol": "sz000001"},
            {"index_symbol": "sh000300", "symbol": "sz000001", "limit": 0},
            {"index_symbol": "sh000300", "symbol": "sz000001", "start_date": "20260807", "end_date": "20240101"},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(MacroAnalysisError):
                    MacroAnalysisQuery.from_mapping(value)

    def test_live_empty_research_becomes_explicit_neutral_evidence(self):
        runtime = MacroAnalysisRuntime(EmptyResearchFinancialTool())

        result = runtime.run(
            MacroAnalysisQuery(symbol="sh600015", mode="live")
        ).to_mapping()

        analysis = result["report"]["analysis"]
        research = result["report"]["macro_data"]["research"]
        self.assertEqual(analysis["sentiment"]["research_count"], 0)
        self.assertEqual(analysis["sentiment"]["latest_rating"], "not_available")
        self.assertEqual(research["source"], DERIVED_EMPTY_RESEARCH_SOURCE)
        self.assertIn("no research report was available", analysis["caveats"][-1])


if __name__ == "__main__":
    unittest.main()
