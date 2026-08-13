import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "financial_data_hub.json"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (
    SUPPORTED_FINANCIAL_DATASETS,
    FinancialDataError,
    FinancialDataErrorCode,
    FinancialDataHub,
    FinancialDataPolicy,
    FinancialDataRecord,
    FinancialDataTool,
    FixtureFinancialDataProvider,
    JsonFinancialDataCache,
    SlidingWindowRateLimiter,
    SubprocessFinancialDataProvider,
    create_financial_mcp_server,
)
from agent_platform.finance._provider_worker import TZ, _record


NOW = datetime.fromisoformat("2026-08-07T12:30:00+08:00")


def provider_payload(dataset="macro.gdp"):
    return {
        "dataset": dataset,
        "source": "fake.provider",
        "timestamp": NOW.isoformat(),
        "records": [
            {
                "subject": "demo",
                "fields": {"value": "5.2"},
                "source": "fake.provider",
                "timestamp": NOW.isoformat(),
                "as_of": "2025-07-15T00:00:00+08:00",
            }
        ],
    }


class FakeProvider:
    def __init__(self, outcomes=None):
        self.calls = 0
        self.outcomes = list(outcomes or [provider_payload()])

    def source_for(self, dataset):
        del dataset
        return "fake.provider"

    def fetch(self, dataset, params, *, timeout_seconds):
        del dataset, params, timeout_seconds
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FinancialDataHubTests(unittest.TestCase):
    def test_realtime_record_accepts_only_small_explicit_provider_clock_skew(self):
        fetched_at = datetime.now(TZ)
        provider_time = fetched_at + timedelta(seconds=5)

        record = _record(
            subject="sh600000",
            fields={"last": "9.17"},
            source="tencent.qt.gtimg.cn",
            timestamp=fetched_at,
            as_of=provider_time,
            future_tolerance_seconds=10,
        )

        self.assertEqual(record["as_of"], provider_time.isoformat())
        self.assertEqual(record["timestamp"], provider_time.isoformat())

    def test_realtime_record_still_rejects_large_future_timestamp(self):
        fetched_at = datetime.now(TZ)

        with self.assertRaisesRegex(ValueError, "later than fetch time"):
            _record(
                subject="sh600000",
                fields={"last": "9.17"},
                source="tencent.qt.gtimg.cn",
                timestamp=fetched_at,
                as_of=fetched_at + timedelta(seconds=11),
                future_tolerance_seconds=10,
            )

    def test_offline_fixture_covers_every_b1_dataset_with_provenance(self):
        provider = FixtureFinancialDataProvider(FIXTURE_PATH)
        hub = FinancialDataHub(
            live_provider=FakeProvider(),
            offline_provider=provider,
            clock=lambda: NOW,
        )
        tool = FinancialDataTool(hub)

        for dataset in SUPPORTED_FINANCIAL_DATASETS:
            with self.subTest(dataset=dataset):
                output = tool.run({"dataset": dataset})
                self.assertEqual(output["dataset"], dataset)
                self.assertEqual(output["mode"], "offline")
                self.assertGreater(output["record_count"], 0)
                record = output["records"][0]
                self.assertTrue(record["source"])
                self.assertIn("+08:00", record["timestamp"])
                self.assertIn("+08:00", record["as_of"])

        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        provider_entries = {
            entry["dataset"]: entry for entry in fixture["datasets"]
            if entry["dataset"] in {
                "macro.policy_lpr",
                "sentiment.research",
                "tushare.daily",
            }
        }
        self.assertEqual(
            provider_entries["macro.policy_lpr"]["source"],
            "akshare.macro_china_lpr",
        )
        self.assertEqual(
            provider_entries["sentiment.research"]["source"],
            "akshare.stock_research_report_em",
        )
        self.assertEqual(
            provider_entries["tushare.daily"]["source"],
            "tushare.pro.daily",
        )

    def test_cache_hit_skips_the_second_provider_call(self):
        provider = FakeProvider([provider_payload(), provider_payload()])
        with tempfile.TemporaryDirectory() as temporary:
            hub = FinancialDataHub(
                live_provider=provider,
                offline_provider=provider,
                cache=JsonFinancialDataCache(Path(temporary) / "cache.json"),
                clock=lambda: NOW,
            )

            first = hub.fetch("macro.gdp", {}, mode="live")
            second = hub.fetch("macro.gdp", {}, mode="live")

        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(second.attempts, 0)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(second.trace[0].event, "cache.hit")

    def test_retry_is_finite_and_preserves_stable_error(self):
        unavailable = FinancialDataError(
            "temporary outage",
            code=FinancialDataErrorCode.PROVIDER_UNAVAILABLE,
            source="fake.provider",
        )
        provider = FakeProvider([unavailable, provider_payload()])
        sleeps = []
        hub = FinancialDataHub(
            live_provider=provider,
            offline_provider=provider,
            policy=FinancialDataPolicy(max_attempts=2, backoff_seconds=0.1),
            clock=lambda: NOW,
            sleeper=sleeps.append,
        )

        result = hub.fetch("macro.gdp", {}, mode="live")

        self.assertEqual(result.attempts, 2)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(sleeps, [0.1])

    def test_rate_limiter_rejects_excess_calls_without_waiting(self):
        provider = FakeProvider([provider_payload(), provider_payload()])
        limiter = SlidingWindowRateLimiter(1, 60, clock=lambda: 1.0)
        hub = FinancialDataHub(
            live_provider=provider,
            offline_provider=provider,
            limiter=limiter,
            policy=FinancialDataPolicy(max_attempts=1),
            clock=lambda: NOW,
        )

        hub.fetch("macro.gdp", {"request": 1}, mode="live")
        with self.assertRaises(FinancialDataError) as raised:
            hub.fetch("macro.gdp", {"request": 2}, mode="live")

        self.assertEqual(raised.exception.code, FinancialDataErrorCode.RATE_LIMITED)
        self.assertEqual(provider.calls, 1)

    def test_subprocess_provider_enforces_a_hard_total_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary) / "slow_worker.py"
            worker.write_text(
                "import sys, time\nsys.stdin.read()\ntime.sleep(5)\n",
                encoding="utf-8",
            )
            provider = SubprocessFinancialDataProvider(worker)

            with self.assertRaises(FinancialDataError) as raised:
                provider.fetch("macro.gdp", {}, timeout_seconds=0.1)

        self.assertEqual(raised.exception.code, FinancialDataErrorCode.TIMEOUT)

    def test_subprocess_provider_normalizes_tushare_auth_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary) / "auth_worker.py"
            worker.write_text(
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'error': {'type': 'RuntimeError', "
                "'message': 'TUSHARE_TOKEN is required'}}))\n"
                "raise SystemExit(2)\n",
                encoding="utf-8",
            )
            provider = SubprocessFinancialDataProvider(worker)

            with self.assertRaises(FinancialDataError) as raised:
                provider.fetch("sentiment.research", {}, timeout_seconds=2)

        self.assertEqual(raised.exception.code, FinancialDataErrorCode.AUTH_REQUIRED)

    def test_subprocess_provider_normalizes_empty_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary) / "empty_worker.py"
            worker.write_text(
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'error': {'type': 'ValueError', "
                "'message': 'provider returned no records'}}))\n"
                "raise SystemExit(2)\n",
                encoding="utf-8",
            )
            provider = SubprocessFinancialDataProvider(worker)

            with self.assertRaises(FinancialDataError) as raised:
                provider.fetch("sentiment.research", {}, timeout_seconds=2)

        self.assertEqual(raised.exception.code, FinancialDataErrorCode.EMPTY_RESPONSE)

    def test_subprocess_provider_normalizes_tushare_permission_denied(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary) / "permission_worker.py"
            worker.write_text(
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'error': {'type': 'Exception', "
                "'message': '抱歉，您没有接口(daily)访问权限'}}))\n"
                "raise SystemExit(2)\n",
                encoding="utf-8",
            )
            provider = SubprocessFinancialDataProvider(worker)

            with self.assertRaises(FinancialDataError) as raised:
                provider.fetch("tushare.daily", {}, timeout_seconds=2)

        self.assertEqual(
            raised.exception.code,
            FinancialDataErrorCode.PERMISSION_DENIED,
        )

    def test_record_rejects_future_or_non_json_data(self):
        invalid_values = (
            {
                "subject": "demo",
                "fields": {"bad": object()},
                "source": "fixture",
                "timestamp": NOW,
                "as_of": NOW,
            },
            {
                "subject": "demo",
                "fields": {},
                "source": "fixture",
                "timestamp": NOW,
                "as_of": datetime.fromisoformat("2026-08-08T12:30:00+08:00"),
            },
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(FinancialDataError):
                    FinancialDataRecord(**value)

    def test_mcp_server_registers_read_only_financial_tools(self):
        server = create_financial_mcp_server(
            FinancialDataTool(
                FinancialDataHub(
                    live_provider=FakeProvider(),
                    offline_provider=FixtureFinancialDataProvider(FIXTURE_PATH),
                    clock=lambda: NOW,
                )
            )
        )

        tools = asyncio.run(server.list_tools())

        self.assertEqual(server.name, "agent-platform-financial-data")
        self.assertEqual(
            {tool.name for tool in tools},
            {"list_financial_datasets", "get_financial_data"},
        )
        _, structured = asyncio.run(
            server.call_tool("get_financial_data", {"dataset": "macro.gdp"})
        )
        self.assertEqual(structured["dataset"], "macro.gdp")
        self.assertEqual(structured["mode"], "offline")
        self.assertFalse(structured["cache_hit"])


if __name__ == "__main__":
    unittest.main()
