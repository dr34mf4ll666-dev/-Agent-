import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestError,
    BacktestRequest,
    BacktestSignal,
)
from agent_platform.finance.contracts import MarketDataSeries


TZ = timezone(timedelta(hours=8))


def series(*, volumes=(1000, 1000, 1000, 1000), opens=(10, 11, 12, 13)):
    records = []
    for index, (volume, open_price) in enumerate(zip(volumes, opens), start=1):
        as_of = datetime(2026, 1, index, 15, tzinfo=TZ)
        price = Decimal(str(open_price))
        records.append(
            {
                "symbol": "sz000001",
                "open": str(price),
                "high": str(price + Decimal("0.5")),
                "low": str(price - Decimal("0.5")),
                "close": str(price),
                "volume": volume,
                "source": "captured.test",
                "timestamp": "2026-02-01T10:00:00+08:00",
                "as_of": as_of.isoformat(),
            }
        )
    return MarketDataSeries.from_records(records)


def signal(day, target):
    return BacktestSignal(
        symbol="sz000001",
        signal_at=datetime(2026, 1, day, 15, tzinfo=TZ),
        target_position_percent=Decimal(str(target)),
        source="c3.test",
    )


class BacktestTests(unittest.TestCase):
    def test_signal_executes_only_at_next_bar_open(self):
        request = BacktestRequest(
            series=series(),
            signals=(signal(1, 50), signal(3, 0)),
            config=BacktestConfig(
                initial_cash=Decimal("100000"),
                commission_rate=Decimal("0"),
                minimum_commission=Decimal("0"),
                stamp_duty_rate=Decimal("0"),
                slippage_basis_points=Decimal("0"),
            ),
        )

        report = BacktestEngine().run(request).to_mapping()

        self.assertEqual([order["side"] for order in report["orders"]], ["buy", "sell"])
        self.assertEqual(report["orders"][0]["signal_at"], "2026-01-01T15:00:00+08:00")
        self.assertEqual(report["orders"][0]["execution_at"], "2026-01-02T09:30:00+08:00")
        self.assertEqual(report["orders"][0]["raw_open"], "11")
        self.assertEqual(report["orders"][1]["signal_at"], "2026-01-03T15:00:00+08:00")
        self.assertEqual(report["orders"][1]["execution_at"], "2026-01-04T09:30:00+08:00")
        self.assertFalse(report["time_semantics"]["same_bar_execution_allowed"])
        self.assertFalse(report["time_semantics"]["execution_layer_uses_future_data"])
        self.assertFalse(
            report["time_semantics"]["signal_generation_verified_no_future"]
        )

    def test_commission_stamp_duty_and_slippage_are_charged(self):
        request = BacktestRequest(
            series=series(opens=(10, 11, 12, 12)),
            signals=(signal(1, 50), signal(3, 0)),
        )

        report = BacktestEngine().run(request).to_mapping()

        self.assertEqual(report["orders"][0]["stamp_duty"], "0.00")
        self.assertGreater(Decimal(report["orders"][1]["stamp_duty"]), 0)
        self.assertGreater(Decimal(report["costs"]["commission_cny"]), 0)
        self.assertGreater(Decimal(report["costs"]["slippage_cny"]), 0)
        self.assertEqual(report["metrics"]["closed_trade_count"], 1)
        self.assertEqual(report["metrics"]["win_rate_percent"], "100.0000")
        self.assertIsNone(report["metrics"]["profit_loss_ratio"])

    def test_zero_volume_delays_execution_until_next_tradable_bar(self):
        request = BacktestRequest(
            series=series(volumes=(1000, 0, 1000, 1000)),
            signals=(signal(1, 50),),
            config=BacktestConfig(
                commission_rate=Decimal("0"),
                minimum_commission=Decimal("0"),
                stamp_duty_rate=Decimal("0"),
                slippage_basis_points=Decimal("0"),
            ),
        )

        report = BacktestEngine().run(request).to_mapping()

        self.assertEqual(report["orders"][0]["execution_at"], "2026-01-03T09:30:00+08:00")
        self.assertEqual(report["trace"][1]["event"], "execution.skipped.suspended")

    def test_last_bar_signal_remains_pending_instead_of_using_same_close(self):
        request = BacktestRequest(
            series=series(),
            signals=(signal(4, 50),),
        )

        report = BacktestEngine().run(request).to_mapping()

        self.assertEqual(report["executed_order_count"], 0)
        self.assertEqual(report["pending_signal"]["reason"], "no later tradable bar")
        self.assertIsNone(report["metrics"]["win_rate_percent"])

    def test_signal_must_match_a_completed_bar_and_symbol(self):
        invalid_signals = (
            BacktestSignal(
                symbol="sh600000",
                signal_at=datetime(2026, 1, 1, 15, tzinfo=TZ),
                target_position_percent=Decimal("50"),
                source="test",
            ),
            BacktestSignal(
                symbol="sz000001",
                signal_at=datetime(2025, 12, 31, 15, tzinfo=TZ),
                target_position_percent=Decimal("50"),
                source="test",
            ),
        )
        for invalid in invalid_signals:
            with self.subTest(invalid=invalid):
                with self.assertRaises(BacktestError):
                    BacktestRequest(series=series(), signals=(invalid,))

    def test_safety_fields_never_enable_real_orders(self):
        report = BacktestEngine().run(
            BacktestRequest(series=series(), signals=())
        ).to_mapping()

        self.assertTrue(report["simulation_only"])
        self.assertFalse(report["order_created"])
        self.assertFalse(report["real_trading_allowed"])

    def test_integer_config_fields_reject_decimal_values(self):
        for field in ("board_lot", "annual_trading_days"):
            with self.subTest(field=field):
                with self.assertRaises(BacktestError):
                    BacktestConfig(**{field: Decimal("100")})


if __name__ == "__main__":
    unittest.main()
