import sys
import unittest
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.analysis_provenance import (  # noqa: E402
    DataQualityRuntime,
    unknown_provenance,
)


NOW = datetime.fromisoformat("2026-08-18T12:00:00+08:00")
SECURITY = {
    "symbol": "sz000001",
    "code": "000001",
    "name": "平安银行",
    "industry": "银行",
}


def _snapshot(*, statuses=None, timestamp="2026-08-18T11:59:00+08:00"):
    statuses = statuses or {"market.daily": "primary", "macro.snapshot": "primary"}
    datasets = []
    for dataset, status in statuses.items():
        datasets.append(
            {
                "dataset": dataset,
                "required": dataset != "optional.news",
                "status": status,
                "source": "fixture-source" if status != "not_available" else "",
                "timestamp": timestamp if status != "not_available" else None,
                "as_of": "2026-08-18T11:55:00+08:00" if status != "not_available" else None,
                "freshness": "已验证快照" if status == "fixture" else "实时获取",
                "detail": "" if status != "not_available" else "provider timeout",
            }
        )
    return {
        "snapshot_id": "snapshot-001",
        "symbol": "sz000001",
        "mode": "live",
        "acquired_at": "2026-08-18T11:59:00+08:00",
        "as_of": "2026-08-18T11:55:00+08:00",
        "datasets": datasets,
    }


class DataQualityRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = DataQualityRuntime(
            catalog_version="security-master-v3",
            code_version="0.1.0",
        )

    def test_complete_snapshot_is_comparable(self):
        report = self.runtime.evaluate(_snapshot(), SECURITY, NOW)

        self.assertEqual(report.overall_status, "complete")
        self.assertTrue(report.comparison_ready)
        self.assertEqual(report.required_available_count, 2)
        self.assertEqual(report.sources, ("fixture-source",))
        self.assertNotIn("value", report.to_mapping())

    def test_backup_required_data_is_degraded_but_not_blocked(self):
        report = self.runtime.evaluate(
            _snapshot(statuses={"market.daily": "backup", "macro.snapshot": "primary"}),
            SECURITY,
            NOW,
        )

        self.assertEqual(report.overall_status, "degraded")
        self.assertFalse(report.comparison_ready)
        self.assertEqual(report.degraded_count, 1)
        self.assertTrue(report.used_backup)
        self.assertIn("备用", report.items[0]["reason"])

    def test_required_unavailable_data_blocks_report(self):
        report = self.runtime.evaluate(
            _snapshot(statuses={"market.daily": "not_available", "macro.snapshot": "primary"}),
            SECURITY,
            NOW,
        )

        self.assertEqual(report.overall_status, "blocked")
        self.assertFalse(report.comparison_ready)
        self.assertEqual(report.unavailable_count, 1)
        self.assertEqual(report.missing_datasets, ("market.daily",))

    def test_invalid_time_contract_is_blocked(self):
        snapshot = _snapshot()
        snapshot["datasets"][0]["as_of"] = "2026-08-18T12:01:00+08:00"

        report = self.runtime.evaluate(snapshot, SECURITY, NOW)

        self.assertEqual(report.overall_status, "blocked")
        self.assertEqual(report.items[0]["quality_status"], "invalid")

    def test_source_status_inconsistency_is_blocked(self):
        snapshot = _snapshot()
        snapshot["datasets"][0]["source"] = "backup:provider"

        report = self.runtime.evaluate(snapshot, SECURITY, NOW)

        self.assertEqual(report.overall_status, "blocked")
        self.assertIn("来源标识与主源状态不一致", report.items[0]["reason"])

    def test_identity_fingerprint_is_canonical_and_changes_with_input(self):
        first = self.runtime.build_identity(_snapshot())
        second = self.runtime.build_identity(
            {"symbol": "sz000001", "snapshot_id": "snapshot-002"}
        )

        self.assertEqual(first.fingerprint, self.runtime.build_identity(_snapshot()).fingerprint)
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(len(first.fingerprint), 64)

    def test_legacy_projection_has_no_comparison_claim(self):
        value = unknown_provenance(symbol="sz000001")

        self.assertEqual(value["quality"]["overall_status"], "unknown")
        self.assertFalse(value["quality"]["comparison_ready"])
        self.assertIsNone(value["fingerprint"])


if __name__ == "__main__":
    unittest.main()
