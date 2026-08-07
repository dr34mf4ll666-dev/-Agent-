import copy
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "industry_analysis.json"
SAMPLE_REPORT_PATH = (
    PROJECT_ROOT / "docs" / "examples" / "industry-analysis-glass.json"
)
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import CognitiveLoopExecutionError, GraphDefinition, GraphRunner
from agent_platform.finance import (
    IndustryAnalysisError,
    IndustryAnalysisQuery,
    IndustryAnalysisRuntime,
    build_default_industry_analysis_runtime,
    validate_industry_analysis_output,
)


def default_query():
    return IndustryAnalysisQuery(sector="玻璃行业")


class BrokenSourceFinancialTool:
    def run(self, arguments):
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        dataset = arguments["dataset"]
        entry = copy.deepcopy(
            next(item for item in fixture["datasets"] if item["dataset"] == dataset)
        )
        entry["records"][0].pop("source")
        return entry


class IndustryAnalysisRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = build_default_industry_analysis_runtime(
            project_root=PROJECT_ROOT
        )

    def test_offline_runtime_calculates_profile_policy_chain_and_leaders(self):
        result = self.runtime.run(default_query()).to_mapping()
        report = result["report"]
        analysis = report["analysis"]
        expected_sample = json.loads(
            SAMPLE_REPORT_PATH.read_text(encoding="utf-8")
        )

        self.assertEqual(analysis, expected_sample)
        self.assertEqual(analysis["sector"], "玻璃行业")
        self.assertEqual(analysis["industry_profile"]["company_count"], 19)
        self.assertEqual(analysis["industry_profile"]["change_percent"], "4.3554")
        self.assertEqual(analysis["prosperity"]["label"], "hot")
        self.assertEqual(analysis["policy"]["signal"], "stable")
        self.assertEqual(analysis["industry_chain"]["method"], "project_taxonomy_rule")
        self.assertEqual(analysis["leaders"][0]["sector"], "电力行业")
        self.assertEqual(len(analysis["score_components"]), 4)
        self.assertEqual(result["loop"]["steps"], 1)
        self.assertEqual(result["loop"]["allowed_tools"], ["industry_analysis"])
        harness_details = {
            event["detail"] for event in result["loop"]["harness_trace"]
        }
        self.assertIn("industry_output_schema", harness_details)
        self.assertIn("industry_market_sources", harness_details)
        self.assertIn("industry_value_recompute", harness_details)

    def test_cross_validator_rejects_tampered_score(self):
        report = copy.deepcopy(self.runtime.run(default_query()).to_mapping()["report"])
        report["analysis"]["score"] = 99

        validation = validate_industry_analysis_output(report)

        self.assertFalse(validation.valid)
        self.assertIn("does not match", validation.detail)

    def test_missing_source_is_blocked_inside_loop(self):
        runtime = IndustryAnalysisRuntime(BrokenSourceFinancialTool())

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
            start="industry",
            nodes={"industry": self.runtime.run_graph_node},
        )

        result = GraphRunner(graph).run(
            {"industry_query": default_query().to_mapping()}
        )

        self.assertEqual(result.statuses["industry"], "completed")
        self.assertEqual(result.state["industry_report"]["score_label"], "positive")
        self.assertEqual(result.state["industry_loop"]["steps"], 1)
        self.assertIn("industry_snapshot", result.state["industry_evidence"])

    def test_query_rejects_invalid_date_and_limit(self):
        invalid_values = (
            {"sector": "玻璃行业", "mode": "auto"},
            {"sector": "玻璃行业", "limit": 0},
            {"sector": "玻璃行业", "start_date": "20260807", "end_date": "20260101"},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(IndustryAnalysisError):
                    IndustryAnalysisQuery.from_mapping(value)


if __name__ == "__main__":
    unittest.main()
