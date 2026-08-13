import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.finance import (  # noqa: E402
    AnalysisSnapshot,
    AnalysisSnapshotError,
    AnalysisSnapshotRuntime,
    CombinedAnalysisQuery,
    FinancialDataError,
    FinancialDataErrorCode,
    JsonSnapshotFallbackCache,
)


def _payload(dataset, params, *, source="primary", cache_hit=False):
    subject = params.get("symbol", params.get("sector", "CN"))
    fields = {"value": "1"}
    if dataset == "market.daily":
        fields = {
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10.5",
            "volume_shares": "1000",
        }
    return {
        "dataset": dataset,
        "record_count": 1,
        "source": source,
        "timestamp": "2026-08-13T12:00:00+08:00",
        "attempts": 1,
        "cache_hit": cache_hit,
        "mode": "live",
        "records": [{
            "subject": subject,
            "fields": fields,
            "source": source,
            "timestamp": "2026-08-13T12:00:00+08:00",
            "as_of": "2026-08-13T11:00:00+08:00",
        }],
        "trace": [],
    }


class _Source:
    def __init__(self, *, fail=(), source="primary", cache_hit=False):
        self.fail = set(fail)
        self.source = source
        self.cache_hit = cache_hit
        self.calls = []

    def fetch(self, dataset, params, *, mode):
        del mode
        key = (dataset, json.dumps(dict(params), sort_keys=True, ensure_ascii=False))
        self.calls.append(key)
        if dataset in self.fail:
            raise FinancialDataError(
                f"{self.source} failed",
                code=FinancialDataErrorCode.PROVIDER_UNAVAILABLE,
                source=self.source,
            )
        return _payload(dataset, params, source=self.source, cache_hit=self.cache_hit)


class AnalysisSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.query = CombinedAnalysisQuery.for_symbol(
            symbol="sz000001",
            sector="金融行业",
            mode="live",
            start_date="20240101",
            end_date="20260813",
        )
        self.now = lambda: datetime(2026, 8, 13, 12, tzinfo=ZoneInfo("Asia/Shanghai"))

    def test_primary_success_freezes_unique_requests(self):
        primary = _Source()
        runtime = AnalysisSnapshotRuntime(primary=primary, now=self.now)

        snapshot = runtime.acquire(self.query)

        self.assertEqual(len(primary.calls), len(set(primary.calls)))
        self.assertEqual(len(primary.calls), 14)
        self.assertTrue(all(item.status == "primary" for item in snapshot.datasets))
        self.assertFalse(snapshot.degraded)
        self.assertEqual(snapshot.tool().run({
            "dataset": "market.daily",
            "params": {
                "symbol": self.query.technical.symbol,
                "start_date": self.query.technical.start_date,
                "end_date": self.query.technical.end_date,
                "limit": self.query.technical.limit,
            },
        })["source"], "primary")

    def test_backup_is_used_after_primary_failure(self):
        primary = _Source(fail={"market.daily"})
        backup = _Source(source="backup")
        snapshot = AnalysisSnapshotRuntime(
            primary=primary, backup=backup, now=self.now
        ).acquire(self.query)

        daily = next(item for item in snapshot.datasets if item.request.dataset == "market.daily")
        self.assertEqual(daily.status, "backup")
        self.assertEqual(daily.source, "backup")
        self.assertTrue(snapshot.degraded)

    def test_stale_cache_is_used_after_both_sources_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = JsonSnapshotFallbackCache(Path(temp_dir) / "fallback.json")
            warm = AnalysisSnapshotRuntime(primary=_Source(), fallback_cache=cache, now=self.now)
            warm.acquire(self.query)
            snapshot = AnalysisSnapshotRuntime(
                primary=_Source(fail={"market.daily"}),
                backup=_Source(fail={"market.daily"}, source="backup"),
                fallback_cache=cache,
                now=self.now,
            ).acquire(self.query)

        daily = next(item for item in snapshot.datasets if item.request.dataset == "market.daily")
        self.assertEqual(daily.status, "cache_stale")
        self.assertEqual(daily.freshness, "历史缓存降级")

    def test_required_dataset_all_sources_failed_rejects_snapshot(self):
        primary = _Source(fail={"market.daily"})
        backup = _Source(fail={"market.daily"}, source="backup")

        with self.assertRaisesRegex(AnalysisSnapshotError, "market.daily"):
            AnalysisSnapshotRuntime(
                primary=primary, backup=backup, now=self.now
            ).acquire(self.query)

    def test_optional_dataset_becomes_explicit_not_available(self):
        snapshot = AnalysisSnapshotRuntime(
            primary=_Source(fail={"market.fund_flow"}), now=self.now
        ).acquire(self.query)

        fund_flow = next(
            item for item in snapshot.datasets if item.request.dataset == "market.fund_flow"
        )
        self.assertEqual(fund_flow.status, "not_available")
        self.assertIsNone(fund_flow.value)

    def test_snapshot_mapping_is_detached_and_round_trips(self):
        snapshot = AnalysisSnapshotRuntime(primary=_Source(), now=self.now).acquire(self.query)
        mapping = snapshot.to_mapping()
        original_source = snapshot.datasets[0].source
        mapping["datasets"][0]["source"] = "tampered"

        self.assertEqual(snapshot.datasets[0].source, original_source)
        restored = AnalysisSnapshot.from_mapping(snapshot.to_mapping())
        self.assertEqual(restored.snapshot_id, snapshot.snapshot_id)
        self.assertEqual(restored.to_mapping(), snapshot.to_mapping())

    def test_fallback_cache_preserves_concurrent_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = JsonSnapshotFallbackCache(Path(temp_dir) / "fallback.json")
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(
                    lambda index: cache.put(str(index), {"value": index}),
                    range(20),
                ))

            self.assertEqual(
                {cache.get(str(index))["value"] for index in range(20)},
                set(range(20)),
            )


if __name__ == "__main__":
    unittest.main()
