import csv
import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "synthetic_market_bars_30.csv"
)
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import AgentHarness, AgentRequest, HarnessExecutionError
from agent_platform.finance import (
    InsufficientMarketDataError,
    MarketDataSeries,
    TechnicalAnalysisAgent,
    TechnicalAnalysisError,
)


class TechnicalAnalysisAgentTests(unittest.TestCase):
    def _fixture_records(self):
        with FIXTURE_PATH.open(encoding="utf-8", newline="") as fixture_file:
            return list(csv.DictReader(fixture_file))

    def _fixture_series(self):
        return MarketDataSeries.from_records(self._fixture_records())

    def _series_with_closes(self, closes):
        records = self._fixture_records()
        for record, close in zip(records, closes):
            record["open"] = f"{close:.2f}"
            record["high"] = f"{close + Decimal('0.10'):.2f}"
            record["low"] = f"{close - Decimal('0.10'):.2f}"
            record["close"] = f"{close:.2f}"
        return MarketDataSeries.from_records(records)

    def test_agent_calculates_known_indicators_through_the_harness(self):
        request = AgentRequest(
            task="analyze the latest technical trend",
            context={"market_data": self._fixture_series()},
        )

        harness_result = AgentHarness(TechnicalAnalysisAgent()).run(request)
        analysis = harness_result.response.metadata["analysis"]

        self.assertEqual(
            analysis,
            {
                "symbol": "DEMO.SH",
                "as_of": "2026-07-31T15:00:00+08:00",
                "sample_size": 30,
                "latest_close": "12.90",
                "daily_return": "0.0078125",
                "sma_5": "12.70",
                "sma_20": "11.95",
                "trend": "bullish",
                "trend_rule": "latest_close > sma_5 > sma_20",
                "sources": ["synthetic_fixture"],
            },
        )
        self.assertIn("不构成投资建议", harness_result.response.content)
        self.assertEqual(
            [event.event for event in harness_result.trace],
            [
                "preflight.started",
                "preflight.passed",
                "agent.started",
                "agent.finished",
                "postflight.passed",
            ],
        )

    def test_harness_preserves_an_explicit_insufficient_data_error(self):
        short_series = MarketDataSeries.from_records(self._fixture_records()[:5])
        request = AgentRequest(
            task="analyze the latest technical trend",
            context={"market_data": short_series},
        )

        with self.assertRaises(HarnessExecutionError) as raised:
            AgentHarness(TechnicalAnalysisAgent()).run(request)

        self.assertIsInstance(raised.exception.cause, InsufficientMarketDataError)
        self.assertEqual(raised.exception.cause.required, 20)
        self.assertEqual(raised.exception.cause.actual, 5)
        self.assertEqual(
            [event.event for event in raised.exception.trace],
            [
                "preflight.started",
                "preflight.passed",
                "agent.started",
                "agent.failed",
            ],
        )

    def test_agent_rejects_missing_or_wrong_market_data_context(self):
        invalid_contexts = ({}, {"market_data": []})

        for context in invalid_contexts:
            with self.subTest(context=context):
                request = AgentRequest(task="analyze technical trend", context=context)

                with self.assertRaises(HarnessExecutionError) as raised:
                    AgentHarness(TechnicalAnalysisAgent()).run(request)

                self.assertIsInstance(raised.exception.cause, TechnicalAnalysisError)
                self.assertIn("market_data", str(raised.exception.cause))

    def test_agent_explains_a_bearish_trend_with_its_rule(self):
        closes = tuple(
            Decimal("12.90") - Decimal("0.10") * index
            for index in range(30)
        )
        request = AgentRequest(
            task="analyze a falling technical trend",
            context={"market_data": self._series_with_closes(closes)},
        )

        analysis = (
            AgentHarness(TechnicalAnalysisAgent())
            .run(request)
            .response.metadata["analysis"]
        )

        self.assertEqual(analysis["trend"], "bearish")
        self.assertEqual(
            analysis["trend_rule"],
            "latest_close < sma_5 < sma_20",
        )


if __name__ == "__main__":
    unittest.main()
