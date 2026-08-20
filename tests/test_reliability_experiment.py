import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.reliability_experiment import (  # noqa: E402
    OfflineReliabilityExperimentRuntime,
)


class OfflineReliabilityExperimentTests(unittest.TestCase):
    def test_fixed_experiment_covers_all_required_failure_paths(self):
        result = OfflineReliabilityExperimentRuntime().run()

        self.assertFalse(result["network_used"])
        self.assertFalse(result["file_output"])
        self.assertEqual(
            [item["id"] for item in result["scenarios"]],
            [
                "normal_analysis",
                "source_timeout_retry",
                "cache_degradation",
                "checkpoint_recovery",
                "output_validation_failure",
            ],
        )
        self.assertEqual(result["metrics"]["success_rate_percent"], 80.0)
        self.assertEqual(result["metrics"]["fault_recovery_rate_percent"], 100.0)
        self.assertEqual(result["metrics"]["duplicate_successful_node_count"], 0)
        self.assertEqual(result["metrics"]["p50_latency_ms"], 510)
        self.assertEqual(result["metrics"]["p95_latency_ms"], 760)
        self.assertEqual(result["metrics"]["p99_latency_ms"], 760)

    def test_observability_trace_contains_rejection_and_recovery_evidence(self):
        result = OfflineReliabilityExperimentRuntime().run()
        overview = result["observability"]

        self.assertEqual(overview["metrics"]["trace_count"], 5)
        self.assertEqual(overview["metrics"]["success_rate_percent"], 80.0)
        self.assertGreaterEqual(overview["metrics"]["retry_rate_percent"], 0)
        validation = next(
            item for item in result["scenarios"] if item["id"] == "output_validation_failure"
        )
        self.assertEqual(validation["status"], "failed")
        self.assertEqual(
            next(
                item for item in overview["recent_traces"]
                if item["trace_id"] == validation["trace_id"]
            )["status"],
            "failed",
        )

    def test_comparison_demo_explains_data_and_source_differences(self):
        reasons = OfflineReliabilityExperimentRuntime().run()["comparison"]["change_reasons"]

        self.assertEqual(
            {item["id"] for item in reasons},
            {"market_data_changed", "data_source_status_changed"},
        )


if __name__ == "__main__":
    unittest.main()
