import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (
    BacktestConfig,
    BacktestEngine,
    BacktestError,
    BacktestRequest,
    BacktestSignal,
    CorporateAction,
    MarketDataSeries,
    TradingSessionConstraint,
)
from agent_platform.finance.backtest_experiment import (
    BacktestExperimentConfig,
    BacktestExperimentError,
    BacktestExperimentRunner,
    C3BacktestSignalAdapter,
    C3DecisionSnapshot,
    PointInTimeEvidence,
)


TZ = timezone(timedelta(hours=8))


def market_series(symbol="sz000001", opens=(10, 11, 12, 13)):
    records = []
    for index, price_value in enumerate(opens, start=1):
        price = Decimal(str(price_value))
        records.append(
            {
                "symbol": symbol,
                "open": str(price),
                "high": str(price),
                "low": str(price),
                "close": str(price),
                "volume": 100000,
                "source": "captured.real.test",
                "timestamp": "2026-02-01T12:00:00+08:00",
                "as_of": datetime(2026, 1, index, 15, tzinfo=TZ).isoformat(),
            }
        )
    return MarketDataSeries.from_records(records)


def c3_report(symbol, action, position):
    decision = {
        "status": "approved",
        "requested_action": action,
        "approved_action": action,
        "reason": "test",
    }
    return {
        "status": "financial_graph_completed",
        "symbol": symbol,
        "mode": "offline",
        "research": {"report": {"symbol": symbol, "mode": "offline"}},
        "trader": {
            "report": {
                "symbol": symbol,
                "mode": "offline",
                "signal": {"action": action},
                "market_context": {"regime": "risk_on"},
            }
        },
        "route": {"selected_path": "risk_review"},
        "risk_manager": {
            "report": {
                "risk_decision": decision,
                "position": {"approved_percent": str(position)},
            }
        },
        "final_decision": decision,
        "decision_source": "risk_manager",
        "simulation_only": True,
        "order_created": False,
        "real_trading_allowed": False,
    }


def snapshot(symbol, day, action, position):
    signal_at = datetime(2026, 1, day, 15, tzinfo=TZ)
    return C3DecisionSnapshot(
        signal_at=signal_at,
        generated_at=signal_at + timedelta(minutes=5),
        report=c3_report(symbol, action, position),
        evidence=(
            PointInTimeEvidence(
                name="completed_bar",
                source="captured.real.test",
                as_of=signal_at,
                available_at=signal_at,
            ),
        ),
    )


class CompletedBacktestEngineTests(unittest.TestCase):
    def test_signal_created_after_next_open_waits_for_following_open(self):
        series = market_series()
        signal = BacktestSignal(
            symbol=series.symbol,
            signal_at=series.bars[0].as_of,
            available_at=series.bars[1].as_of.replace(hour=10),
            target_position_percent=Decimal("50"),
            source="c3.test",
        )

        report = BacktestEngine().run(
            BacktestRequest(series=series, signals=(signal,))
        ).to_mapping()

        self.assertEqual(
            report["orders"][0]["execution_at"],
            "2026-01-03T09:30:00+08:00",
        )
        self.assertEqual(
            report["market_constraints"]["blocked_executions"][0]["event"],
            "execution.skipped.signal_not_available",
        )

    def test_directional_market_constraint_delays_only_blocked_side(self):
        series = market_series()
        constraint = TradingSessionConstraint(
            symbol=series.symbol,
            as_of=series.bars[1].as_of,
            buy_allowed=False,
            sell_allowed=True,
            reason="limit up",
            source="exchange.test",
            timestamp=series.bars[1].timestamp,
        )
        signal = BacktestSignal(
            symbol=series.symbol,
            signal_at=series.bars[0].as_of,
            target_position_percent=Decimal("50"),
            source="c3.test",
        )

        report = BacktestEngine().run(
            BacktestRequest(
                series=series,
                signals=(signal,),
                trading_constraints=(constraint,),
            )
        ).to_mapping()

        self.assertEqual(
            report["orders"][0]["execution_at"],
            "2026-01-03T09:30:00+08:00",
        )
        self.assertEqual(
            report["market_constraints"]["blocked_execution_count"], 1
        )

    def test_dividend_and_share_multiplier_adjust_cash_shares_and_cost_basis(self):
        series = market_series(opens=(10, 10, 5, 5))
        action = CorporateAction(
            symbol=series.symbol,
            as_of=series.bars[2].as_of,
            announced_at=series.bars[1].as_of,
            cash_dividend_per_share=Decimal("1"),
            share_multiplier=Decimal("2"),
            source="corporate_action.test",
            timestamp=series.bars[2].timestamp,
        )
        signals = (
            BacktestSignal(
                symbol=series.symbol,
                signal_at=series.bars[0].as_of,
                target_position_percent=Decimal("50"),
                source="c3.test",
            ),
            BacktestSignal(
                symbol=series.symbol,
                signal_at=series.bars[2].as_of,
                target_position_percent=Decimal("0"),
                source="c3.test",
            ),
        )
        config = BacktestConfig(
            commission_rate=Decimal("0"),
            minimum_commission=Decimal("0"),
            stamp_duty_rate=Decimal("0"),
            slippage_basis_points=Decimal("0"),
        )

        report = BacktestEngine().run(
            BacktestRequest(
                series=series,
                signals=signals,
                config=config,
                corporate_actions=(action,),
            )
        ).to_mapping()

        event = report["corporate_actions"]["events"][0]
        self.assertEqual(event["shares_before"], 5000)
        self.assertEqual(event["shares_after"], 10000)
        self.assertEqual(event["cash_received"], "5000.00")
        self.assertEqual(report["final_portfolio"]["equity"], "105000.00")
        self.assertGreater(Decimal(report["orders"][1]["realized_pnl"]), 0)

    def test_constraint_must_be_known_by_open(self):
        series = market_series()
        late = TradingSessionConstraint(
            symbol=series.symbol,
            as_of=series.bars[1].as_of,
            available_at=series.bars[1].as_of.replace(hour=10),
            buy_allowed=False,
            sell_allowed=True,
            reason="late status",
            source="exchange.test",
            timestamp=series.bars[1].timestamp,
        )
        with self.assertRaisesRegex(BacktestError, "available by the execution time"):
            BacktestRequest(
                series=series,
                signals=(),
                trading_constraints=(late,),
            )


class PointInTimeC3Tests(unittest.TestCase):
    def test_future_evidence_is_rejected(self):
        signal_at = datetime(2026, 1, 1, 15, tzinfo=TZ)
        with self.assertRaisesRegex(
            BacktestExperimentError,
            "later than the completed signal bar",
        ):
            C3DecisionSnapshot(
                signal_at=signal_at,
                generated_at=signal_at + timedelta(minutes=5),
                report=c3_report("sz000001", "buy", "15"),
                evidence=(
                    PointInTimeEvidence(
                        name="future",
                        source="test",
                        as_of=signal_at + timedelta(days=1),
                        available_at=signal_at + timedelta(days=1),
                    ),
                ),
            )

    def test_tampered_c3_report_is_rejected(self):
        report = c3_report("sz000001", "buy", "15")
        report["order_created"] = True
        signal_at = datetime(2026, 1, 1, 15, tzinfo=TZ)
        with self.assertRaisesRegex(BacktestExperimentError, "safety field"):
            C3DecisionSnapshot(
                signal_at=signal_at,
                generated_at=signal_at + timedelta(minutes=5),
                report=report,
                evidence=(
                    PointInTimeEvidence(
                        name="bar",
                        source="test",
                        as_of=signal_at,
                        available_at=signal_at,
                    ),
                ),
            )

    def test_adapter_maps_risk_approved_position_and_generation_time(self):
        item = snapshot("sz000001", 1, "buy", "15")

        signals = C3BacktestSignalAdapter().convert((item,))

        self.assertEqual(signals[0].target_position_percent, Decimal("15"))
        self.assertEqual(signals[0].available_at, item.generated_at)
        self.assertTrue(signals[0].source.startswith("c3.financial_graph:"))


class FixedExperimentTests(unittest.TestCase):
    def test_fixed_real_fixture_contains_three_stocks_and_benchmark(self):
        path = PROJECT_ROOT / "tests" / "fixtures" / "d1_real_market_pool.json"
        payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["dataset_type"], "captured_real_sample")
        self.assertEqual(len(payload["symbols"]), 3)
        self.assertEqual(
            [len(payload["series"][symbol]) for symbol in payload["symbols"]],
            [243, 243, 243],
        )
        self.assertEqual(len(payload["benchmark"]), 243)
        self.assertEqual(
            {row["source"] for rows in payload["series"].values() for row in rows},
            {"akshare.stock_zh_a_hist_tx"},
        )

    def test_runner_reports_fixed_pool_benchmark_costs_and_baseline(self):
        config = BacktestExperimentConfig.from_mapping(
            {
                "name": "test",
                "symbols": ["sz000001", "sh600000"],
                "benchmark_symbol": "sh000300",
                "start_date": "20260101",
                "end_date": "20260104",
                "initial_cash": "200000",
                "sharpe_baseline": "0.5",
                "execution": {
                    "commission_rate": "0",
                    "minimum_commission": "0",
                    "stamp_duty_rate": "0",
                    "slippage_basis_points": "0",
                    "board_lot": 100,
                    "annual_trading_days": 252,
                },
                "signal_policy": {
                    "kind": "test",
                    "warmup_bars": 2,
                    "rebalance_every_bars": 1,
                    "short_window": 1,
                    "long_window": 2,
                    "positive_target_percent": "50",
                    "negative_target_percent": "0",
                },
            }
        )
        data = {
            "sz000001": market_series("sz000001"),
            "sh600000": market_series("sh600000", opens=(20, 21, 22, 23)),
        }
        decisions = {
            symbol: (
                snapshot(symbol, 1, "buy", "50"),
                snapshot(symbol, 3, "sell", "0"),
            )
            for symbol in data
        }
        benchmark = market_series("sh000300", opens=(100, 101, 102, 103))

        report = BacktestExperimentRunner().run(
            config=config,
            market_data=data,
            decisions=decisions,
            benchmark=benchmark,
        ).to_mapping()

        self.assertEqual(report["status"], "fixed_backtest_experiment_completed")
        self.assertEqual(set(report["per_symbol"]), set(config.symbols))
        self.assertEqual(report["benchmark"]["symbol"], "sh000300")
        self.assertEqual(report["costs"]["total_cny"], "0.00")
        self.assertEqual(report["costs"]["slippage_cny"], "0.00")
        self.assertIn(
            "excess_return_vs_benchmark_percent",
            report["portfolio_metrics"],
        )
        self.assertIn("met", report["sharpe_baseline"])
        self.assertTrue(report["simulation_only"])
        self.assertFalse(report["order_created"])


if __name__ == "__main__":
    unittest.main()
