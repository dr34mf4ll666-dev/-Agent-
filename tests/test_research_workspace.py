import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.report_views import ReportViewRuntime  # noqa: E402
from agent_platform.research_workspace import (  # noqa: E402
    InMemoryResearchWorkspaceStore,
    JsonResearchWorkspaceStore,
    ResearchWorkspaceError,
    ResearchWorkspaceRuntime,
    WorkspacePreferences,
)
from tests.test_report_views import _archive  # noqa: E402


class _Repository:
    def __init__(self, archives):
        self.archives = {item["report_id"]: deepcopy(item) for item in archives}

    def list_reports(self, *, limit=12):
        values = sorted(
            self.archives.values(), key=lambda item: item["archived_at"], reverse=True
        )[:limit]
        return [
            {
                "report_id": item["report_id"],
                "report_version": item["report_version"],
                "job_id": item.get("job_id", "j" * 32),
                "task_status": "succeeded",
                "symbol": item["result"]["security"]["symbol"],
                "name": item["result"]["security"]["name"],
                "code": item["result"]["security"]["code"],
                "mode": item["result"]["data"]["mode"],
                "data_label": item["result"]["data"]["label"],
                "as_of": item["result"]["data"]["as_of"],
                "snapshot_id": item["result"]["data"]["snapshot_id"],
                "verdict": item["result"]["verdict"]["label"],
                "action": item["result"]["verdict"]["action_label"],
                "archived_at": item["archived_at"],
                "data_health": {
                    "available_count": (item.get("snapshot") or {}).get("available_count"),
                    "dataset_count": (item.get("snapshot") or {}).get("dataset_count"),
                    "degraded": (item.get("snapshot") or {}).get("degraded", False),
                    "unavailable_count": sum(
                        dataset.get("status") == "not_available"
                        for dataset in (item.get("snapshot") or {}).get("datasets", [])
                    ),
                    "degraded_count": sum(
                        dataset.get("status") in {"backup", "cache_stale", "not_available"}
                        for dataset in (item.get("snapshot") or {}).get("datasets", [])
                    ),
                },
            }
            for item in values
        ]

    def get_report(self, report_id):
        if report_id not in self.archives:
            raise ResearchWorkspaceError("历史报告不存在。")
        return deepcopy(self.archives[report_id])


def _second_archive(*, same_security=True):
    value = _archive()
    value["report_id"] = "q" * 32
    value["archived_at"] = "2026-08-08T10:00:00+08:00"
    value["result"]["data"]["as_of"] = "2026-08-07T15:00:00+08:00"
    value["result"]["quote"]["latest_close"] = "12"
    value["result"]["verdict"]["confidence"] = 75
    value["result"]["verdict"]["label"] = "偏强"
    value["result"]["dimensions"][0]["score"] = 20
    if not same_security:
        value["result"]["security"] = {
            "symbol": "sh600000",
            "name": "浦发银行",
            "code": "600000",
            "exchange": "上交所",
        }
    return value


class ResearchWorkspaceRuntimeTests(unittest.TestCase):
    def _runtime(self, *, same_security=True, symbols=None):
        repository = _Repository([_archive(), _second_archive(same_security=same_security)])
        return ResearchWorkspaceRuntime(
            repository,
            ReportViewRuntime(repository),
            InMemoryResearchWorkspaceStore(symbols),
            now=lambda: datetime(2026, 8, 14, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

    def test_watchlist_toggle_is_persistent_and_snapshot_marks_reports(self):
        runtime = self._runtime()
        added = runtime.toggle_watchlist("sz000001")

        self.assertTrue(added["added"])
        self.assertEqual(added["workspace"]["watchlist"][0]["name"], "平安银行")
        self.assertTrue(all(item["in_watchlist"] for item in added["workspace"]["reports"]))

        removed = runtime.toggle_watchlist("sz000001")
        self.assertFalse(removed["added"])
        self.assertEqual(removed["workspace"]["watchlist"], [])

    def test_same_security_compare_explains_time_change_without_model_call(self):
        value = self._runtime().compare("r" * 32, "q" * 32, view="basic")

        self.assertEqual(value["kind"], "same_security_change")
        self.assertIn("谨慎偏强", value["headline"])
        self.assertIn("偏强", value["headline"])
        self.assertEqual(value["changes"][0]["delta"], "+1")
        self.assertTrue(value["frozen_data_only"])
        self.assertFalse(value["model_called"])
        self.assertNotIn("professional", value)

    def test_cross_security_professional_compare_keeps_sources_and_dimensions(self):
        value = self._runtime(same_security=False).compare(
            "r" * 32, "q" * 32, view="professional"
        )

        self.assertEqual(value["kind"], "cross_security")
        self.assertEqual(value["right"]["name"], "浦发银行")
        self.assertFalse(value["changes"][-1]["comparable"])
        self.assertEqual(len(value["professional"]["dimension_changes"]), 4)
        self.assertEqual(value["professional"]["dimension_changes"][0]["delta"], "+10")
        self.assertEqual(value["professional"]["left_sources"], ["fixture"])

    def test_invalid_comparison_and_unknown_symbol_are_rejected(self):
        runtime = self._runtime()
        with self.assertRaisesRegex(ResearchWorkspaceError, "不同的报告"):
            runtime.compare("r" * 32, "r" * 32)
        with self.assertRaisesRegex(ResearchWorkspaceError, "客户分析目录"):
            runtime.toggle_watchlist("sz999999")

    def test_json_store_round_trip_and_rejects_unknown_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workspace.json"
            store = JsonResearchWorkspaceStore(path)
            preferences = WorkspacePreferences(
                ("sz000001", "sh600000"), ("r" * 32,)
            )
            store.save(preferences)
            self.assertEqual(store.load(), preferences)

            path.write_text(
                '{"schema_version": 1, "watchlist": ["sz000001"]}',
                encoding="utf-8",
            )
            self.assertEqual(
                store.load(), WorkspacePreferences(("sz000001",), ())
            )

            path.write_text('{"schema_version": 99, "watchlist": []}', encoding="utf-8")
            with self.assertRaisesRegex(ResearchWorkspaceError, "版本"):
                store.load()

    def test_report_favorite_is_persisted_filtered_and_orphans_are_cleaned(self):
        runtime = self._runtime()
        added = runtime.toggle_favorite("r" * 32)

        self.assertTrue(added["added"])
        favorite = next(
            item for item in added["workspace"]["reports"]
            if item["report_id"] == "r" * 32
        )
        self.assertTrue(favorite["favorite"])
        self.assertEqual(added["workspace"]["favorite_count"], 1)

        removed = runtime.toggle_favorite("r" * 32)
        self.assertFalse(removed["added"])
        self.assertEqual(removed["workspace"]["favorite_count"], 0)

    def test_report_state_distinguishes_expired_partial_and_offline_snapshot(self):
        first = _archive()
        first["snapshot"].update(
            {"available_count": 1, "dataset_count": 2, "degraded": True}
        )
        first["snapshot"]["datasets"].append(
            {"dataset": "market.realtime", "status": "not_available"}
        )
        first["result"]["data"]["mode"] = "live"
        first["result"]["data"]["as_of"] = "2026-08-01T15:00:00+08:00"
        second = _second_archive()
        repository = _Repository([first, second])
        runtime = ResearchWorkspaceRuntime(
            repository,
            ReportViewRuntime(repository),
            InMemoryResearchWorkspaceStore(),
            now=lambda: datetime(2026, 8, 14, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

        reports = runtime.snapshot()["reports"]
        expired = next(item for item in reports if item["report_id"] == "r" * 32)
        frozen = next(item for item in reports if item["report_id"] == "q" * 32)
        self.assertEqual(expired["state"]["freshness"]["status"], "expired")
        self.assertEqual(expired["state"]["availability"]["status"], "partial")
        self.assertEqual(frozen["state"]["freshness"]["status"], "snapshot")

    def test_basic_and_professional_exports_keep_their_information_depth(self):
        runtime = self._runtime()

        basic = runtime.export_report("r" * 32, view="basic")
        professional = runtime.export_report("r" * 32, view="professional")
        comparison = runtime.export_comparison(
            "r" * 32, "q" * 32, view="professional"
        )

        self.assertIn("平安银行", basic["content"])
        self.assertIn("研究摘要", basic["content"])
        self.assertNotIn("证据来源", basic["content"])
        self.assertIn("证据来源", professional["content"])
        self.assertIn("风险边界", professional["content"])
        self.assertIn("四个研究维度", professional["content"])
        self.assertIn("同一股票前后变化", comparison["content"])
        self.assertIn("计划仓位上限", comparison["content"])
        self.assertIn("预计单次亏损", comparison["content"])
        self.assertNotIn("<script", professional["content"])
        self.assertTrue(basic["frozen_data_only"])
        self.assertFalse(basic["model_called"])


if __name__ == "__main__":
    unittest.main()
