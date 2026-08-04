import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "synthetic_market_bars.json"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import MarketDataSeries, MarketDataValidationError


class FinanceContractTests(unittest.TestCase):
    def _valid_record(self):
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        return dict(payload["bars"][0])

    def test_synthetic_fixture_becomes_a_traceable_market_data_series(self):
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

        series = MarketDataSeries.from_records(payload["bars"])

        self.assertEqual(payload["dataset_type"], "synthetic_fixture")
        self.assertEqual(series.symbol, "DEMO.SH")
        self.assertEqual(len(series.bars), 5)
        self.assertEqual(series.bars[0].close, Decimal("10.20"))
        self.assertEqual(series.bars[0].source, "synthetic_fixture")
        self.assertEqual(series.bars[0].as_of.isoformat(), "2026-07-27T15:00:00+08:00")
        self.assertEqual(
            series.bars[-1].timestamp.isoformat(),
            "2026-07-31T16:00:00+08:00",
        )

    def test_bar_rejects_a_high_price_below_its_close(self):
        record = self._valid_record()
        record["high"] = "10.10"

        with self.assertRaisesRegex(MarketDataValidationError, "high"):
            MarketDataSeries.from_records([record])

    def test_bar_rejects_a_low_price_above_its_open(self):
        record = self._valid_record()
        record["low"] = "10.10"

        with self.assertRaisesRegex(MarketDataValidationError, "low"):
            MarketDataSeries.from_records([record])

    def test_bar_rejects_non_positive_prices_and_negative_volume(self):
        invalid_values = (
            ("open", "0"),
            ("low", "-0.01"),
            ("volume", -1),
        )

        for field, value in invalid_values:
            with self.subTest(field=field):
                record = self._valid_record()
                record[field] = value

                with self.assertRaisesRegex(MarketDataValidationError, field):
                    MarketDataSeries.from_records([record])

    def test_bar_reports_a_missing_provenance_field(self):
        record = self._valid_record()
        del record["source"]

        with self.assertRaisesRegex(
            MarketDataValidationError,
            "missing required field: source",
        ):
            MarketDataSeries.from_records([record])

    def test_bar_reports_invalid_external_field_formats(self):
        invalid_values = (
            ("close", "not-a-price"),
            ("volume", 100.5),
            ("timestamp", "not-a-time"),
        )

        for field, value in invalid_values:
            with self.subTest(field=field):
                record = self._valid_record()
                record[field] = value

                with self.assertRaisesRegex(MarketDataValidationError, field):
                    MarketDataSeries.from_records([record])

    def test_bar_requires_traceable_identity_and_time_semantics(self):
        invalid_values = (
            ("symbol", " ", "symbol"),
            ("source", "", "source"),
            ("timestamp", "2026-07-27T16:00:00", "timestamp"),
            ("as_of", "2026-07-27T15:00:00", "as_of"),
            ("as_of", "2026-07-27T17:00:00+08:00", "as_of"),
        )

        for field, value, expected_message in invalid_values:
            with self.subTest(field=field, value=value):
                record = self._valid_record()
                record[field] = value

                with self.assertRaisesRegex(
                    MarketDataValidationError,
                    expected_message,
                ):
                    MarketDataSeries.from_records([record])

    def test_series_rejects_an_empty_record_collection(self):
        with self.assertRaisesRegex(MarketDataValidationError, "at least one"):
            MarketDataSeries.from_records([])

    def test_series_rejects_records_for_different_symbols(self):
        first = self._valid_record()
        second = self._valid_record()
        second["symbol"] = "OTHER.SZ"

        with self.assertRaisesRegex(MarketDataValidationError, "one symbol"):
            MarketDataSeries.from_records([first, second])

    def test_series_requires_strictly_increasing_as_of_times(self):
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        first = dict(payload["bars"][0])
        second = dict(payload["bars"][1])

        invalid_orders = (
            [second, first],
            [first, dict(first)],
        )

        for records in invalid_orders:
            with self.subTest(as_of=[record["as_of"] for record in records]):
                with self.assertRaisesRegex(
                    MarketDataValidationError,
                    "strictly increasing",
                ):
                    MarketDataSeries.from_records(records)


if __name__ == "__main__":
    unittest.main()
