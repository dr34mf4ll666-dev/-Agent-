import copy
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "technical_market_daily_30.json"
SAMPLE_REPORT_PATH = (
    PROJECT_ROOT / "docs" / "examples" / "technical-analysis-sz000001.json"
)
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    CognitiveLoopExecutionError,
    GraphDefinition,
    GraphRunner,
)
from agent_platform.finance import (
    TechnicalAnalysisError,
    TechnicalAnalysisQuery,
    TechnicalAnalysisRuntime,
    build_default_technical_analysis_runtime,
    validate_technical_analysis_output,
)


def default_query():
    return TechnicalAnalysisQuery(
        symbol="sz000001",
        start_date="20260626",
        end_date="20260806",
    )


class BrokenSourceFinancialTool:
    def run(self, arguments):
        del arguments
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        entry = copy.deepcopy(fixture["datasets"][0])
        entry["records"][0].pop("source")
        return entry


class TechnicalAnalysisRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = build_default_technical_analysis_runtime(
            project_root=PROJECT_ROOT
        )

    def test_offline_runtime_consumes_data_hub_and_calculates_full_report(self):
        result = self.runtime.run(default_query()).to_mapping()
        analysis = result["report"]["analysis"]
        expected_report = json.loads(SAMPLE_REPORT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(analysis, expected_report)
        self.assertEqual(analysis["symbol"], "sz000001")
        self.assertEqual(analysis["sample_size"], 30)
        self.assertEqual(analysis["as_of"], "2026-08-06T15:00:00+08:00")
        self.assertEqual(
            analysis["sources"], ["akshare.stock_zh_a_hist_tx"]
        )
        self.assertEqual(
            analysis["ma"],
            {"sma_5": "11.4420", "sma_10": "11.3510", "sma_20": "11.0730"},
        )
        self.assertEqual(
            analysis["macd"],
            {"dif": "0.2573", "dea": "0.2559", "histogram": "0.0028"},
        )
        self.assertEqual(analysis["rsi"], {"rsi_14": "62.2919"})
        self.assertEqual(
            analysis["kdj"],
            {"k": "58.5986", "d": "75.1683", "j": "25.4594"},
        )
        self.assertEqual(
            analysis["bollinger"],
            {"middle": "11.0730", "upper": "11.7492", "lower": "10.3968"},
        )
        self.assertEqual(analysis["signal_score"], 10)
        self.assertEqual(analysis["signal_label"], "neutral")
        self.assertEqual(result["loop"]["steps"], 1)
        self.assertEqual(
            result["loop"]["allowed_tools"], ["technical_market_analysis"]
        )
        self.assertIn(
            "cognitive_loop.completed",
            [event["event"] for event in result["loop"]["trace"]],
        )
        harness_details = {
            event["detail"] for event in result["loop"]["harness_trace"]
        }
        self.assertIn("technical_output_schema", harness_details)
        self.assertIn("technical_market_sources", harness_details)
        self.assertIn("technical_indicator_recompute", harness_details)

    def test_cross_validator_rejects_a_tampered_model_or_tool_value(self):
        report = self.runtime.run(default_query()).report
        tampered = copy.deepcopy(dict(report))
        tampered["analysis"]["signal_score"] = 99

        validation = validate_technical_analysis_output(tampered)

        self.assertFalse(validation.valid)
        self.assertIn("does not match", validation.detail)

    def test_missing_source_is_blocked_inside_loop_harness(self):
        runtime = TechnicalAnalysisRuntime(BrokenSourceFinancialTool())

        with self.assertRaises(CognitiveLoopExecutionError) as raised:
            runtime.run(default_query())

        self.assertIn(
            "cognitive_loop.max_steps_exceeded",
            [event.event for event in raised.exception.trace],
        )
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
            start="technical",
            nodes={"technical": self.runtime.run_graph_node},
        )

        result = GraphRunner(graph).run(
            {"technical_query": default_query().to_mapping()}
        )

        self.assertEqual(result.statuses["technical"], "completed")
        self.assertEqual(result.state["technical_report"]["signal_score"], 10)
        self.assertEqual(len(result.state["technical_evidence"]), 30)
        self.assertEqual(result.state["technical_loop"]["steps"], 1)

    def test_query_rejects_invalid_symbol_date_mode_and_limit(self):
        invalid_values = (
            {"symbol": "000001", "start_date": "20260626", "end_date": "20260806"},
            {"symbol": "sz000001", "start_date": "20260806", "end_date": "20260626"},
            {
                "symbol": "sz000001",
                "start_date": "20260626",
                "end_date": "20260806",
                "mode": "auto",
            },
            {
                "symbol": "sz000001",
                "start_date": "20260626",
                "end_date": "20260806",
                "limit": 29,
            },
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(TechnicalAnalysisError):
                    TechnicalAnalysisQuery.from_mapping(value)


if __name__ == "__main__":
    unittest.main()
