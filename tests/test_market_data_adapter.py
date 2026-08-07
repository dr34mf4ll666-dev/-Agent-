import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "tencent_daily_bars_000001.json"
)
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import ToolRegistry
from agent_platform.finance import (
    AkShareTencentDailyAdapter,
    DailyBarQuery,
    DailyMarketDataTool,
    JsonDailyMarketDataAdapter,
    MarketDataErrorCode,
    MarketDataFetchPolicy,
    MarketDataProviderError,
    MarketDataRequestError,
)


FIXED_FETCH_TIME = datetime.fromisoformat("2026-08-07T10:23:41+08:00")


class FakeDataFrame:
    def __init__(self, records):
        self._records = list(records)

    def to_dict(self, *, orient):
        if orient != "records":
            raise AssertionError("adapter must request record orientation")
        return list(self._records)


def verified_tencent_rows():
    return [
        {
            "date": date(2024, 1, 2),
            "open": 9.39,
            "close": 9.21,
            "high": 9.42,
            "low": 9.21,
            "amount": 1158366.0,
        },
        {
            "date": date(2024, 1, 3),
            "open": 9.19,
            "close": 9.20,
            "high": 9.22,
            "low": 9.15,
            "amount": 733610.0,
        },
    ]


class MarketDataAdapterTests(unittest.TestCase):
    def test_query_requires_market_identity_and_a_bounded_date_range(self):
        query = DailyBarQuery.from_arguments(
            {
                "symbol": "SZ000001",
                "start_date": "20240102",
                "end_date": "20240105",
            }
        )

        self.assertEqual(query.symbol, "sz000001")
        self.assertEqual(query.start_date, date(2024, 1, 2))

        invalid_arguments = (
            {"symbol": "000001", "start_date": "20240102", "end_date": "20240105"},
            {"symbol": "sz000001", "start_date": "20240105", "end_date": "20240102"},
            {"symbol": "sz000001", "start_date": "20230101", "end_date": "20240201"},
            {"symbol": "sz000001", "start_date": "20240102", "end_date": "20240105", "adjust": "qfq"},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(MarketDataRequestError):
                    DailyBarQuery.from_arguments(arguments)

    def test_live_adapter_maps_verified_tencent_shape_and_time_semantics(self):
        calls = []

        def transport(**arguments):
            calls.append(arguments)
            return FakeDataFrame(verified_tencent_rows())

        adapter = AkShareTencentDailyAdapter(
            transport,
            clock=lambda: FIXED_FETCH_TIME,
        )
        query = DailyBarQuery(
            symbol="sz000001",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
        )

        result = adapter.fetch(
            query,
            MarketDataFetchPolicy(timeout_seconds=3, max_attempts=1),
        )

        self.assertEqual(calls[0]["timeout"], 3)
        self.assertEqual(calls[0]["symbol"], "sz000001")
        self.assertEqual(result.source, "akshare.stock_zh_a_hist_tx")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.series.bars[0].close, Decimal("9.21"))
        self.assertEqual(result.series.bars[0].volume, 115836600)
        self.assertEqual(
            result.series.bars[0].as_of.isoformat(),
            "2024-01-02T15:00:00+08:00",
        )
        self.assertEqual(result.series.bars[0].timestamp, FIXED_FETCH_TIME)
        self.assertEqual(
            [event.event for event in result.trace],
            [
                "provider.attempt.started",
                "provider.attempt.succeeded",
                "provider.response.mapped",
            ],
        )

    def test_provider_failure_retries_once_and_preserves_the_cause(self):
        attempts = 0
        sleeps = []

        def unavailable(**arguments):
            del arguments
            nonlocal attempts
            attempts += 1
            raise ConnectionError("upstream disconnected")

        adapter = AkShareTencentDailyAdapter(
            unavailable,
            clock=lambda: FIXED_FETCH_TIME,
            sleeper=sleeps.append,
        )
        query = DailyBarQuery("sz000001", date(2024, 1, 2), date(2024, 1, 3))

        with self.assertRaises(MarketDataProviderError) as raised:
            adapter.fetch(
                query,
                MarketDataFetchPolicy(
                    timeout_seconds=1,
                    max_attempts=2,
                    backoff_seconds=0.1,
                ),
            )

        self.assertEqual(attempts, 2)
        self.assertEqual(sleeps, [0.1])
        self.assertEqual(
            raised.exception.code,
            MarketDataErrorCode.PROVIDER_UNAVAILABLE,
        )
        self.assertEqual(raised.exception.attempts, 2)
        self.assertIsInstance(raised.exception.cause, ConnectionError)

    def test_empty_and_changed_provider_shapes_use_stable_error_codes(self):
        query = DailyBarQuery("sz000001", date(2024, 1, 2), date(2024, 1, 3))
        cases = (
            ([], MarketDataErrorCode.EMPTY_RESPONSE),
            (
                [{"date": "2024-01-02", "open": 9.39}],
                MarketDataErrorCode.SCHEMA_MISMATCH,
            ),
        )

        for records, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                adapter = AkShareTencentDailyAdapter(
                    lambda **_: FakeDataFrame(records),
                    clock=lambda: FIXED_FETCH_TIME,
                )
                with self.assertRaises(MarketDataProviderError) as raised:
                    adapter.fetch(
                        query,
                        MarketDataFetchPolicy(max_attempts=1),
                    )
                self.assertEqual(raised.exception.code, expected_code)

    def test_offline_replay_uses_the_same_tool_interface(self):
        tool = DailyMarketDataTool(JsonDailyMarketDataAdapter(FIXTURE_PATH))
        registry = ToolRegistry([tool])

        output = registry.execute(
            "finance_daily_bars",
            {
                "symbol": "sz000001",
                "start_date": "20240102",
                "end_date": "20240105",
            },
        )

        self.assertEqual(output["bar_count"], 4)
        self.assertEqual(output["bars"][0]["volume"], 115836600)
        self.assertEqual(output["bars"][0]["source"], "akshare.stock_zh_a_hist_tx")
        self.assertEqual(output["source"], "offline_fixture")
        self.assertEqual(output["trace"][0]["event"], "fixture.loaded")
        self.assertEqual(output["query"]["adjust"], "")


if __name__ == "__main__":
    unittest.main()
