import copy
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (
    C1DecisionQuery,
    CombinedAnalysisQuery,
    FinancialGraphQuery,
    JsonPaperTradingLedger,
    PaperTradingCycleRequest,
    PaperTradingError,
    PaperTradingQuote,
    PaperTradingRuntime,
    PaperTradingSessionConfig,
    RiskContext,
    build_default_financial_graph_runtime,
)


class PaperTradingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.confirmed_report = cls._report(human_confirmed=True)
        cls.pending_report = cls._report(human_confirmed=False)

    @classmethod
    def _report(cls, *, human_confirmed):
        query = FinancialGraphQuery(
            c1_query=C1DecisionQuery(CombinedAnalysisQuery.for_symbol()),
            risk_context=RiskContext(
                account_equity="100000",
                current_position_percent="0",
                requested_position_percent="15",
                sector_exposure_other_percent="5",
                current_drawdown_percent="5",
                average_daily_turnover="500000000",
                evaluation_time="2026-08-07T10:00:00+08:00",
                human_confirmed=human_confirmed,
            ),
        )
        return build_default_financial_graph_runtime(
            project_root=PROJECT_ROOT
        ).run(query).to_mapping()["report"]

    @staticmethod
    def config(session_id="paper-test"):
        started_at = datetime.fromisoformat("2026-08-07T09:30:00+08:00")
        return PaperTradingSessionConfig(
            session_id=session_id,
            symbols=("sz000001",),
            initial_cash=Decimal("100000"),
            started_at=started_at,
            planned_end_at=started_at + timedelta(days=7),
        )

    @staticmethod
    def quote(report):
        return PaperTradingQuote.from_financial_report(report)

    def test_confirmed_c3_buy_creates_only_a_local_simulated_fill(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JsonPaperTradingLedger(Path(temp_dir) / "ledger.json")
            runtime = PaperTradingRuntime(store=store)
            result = runtime.run_cycle(
                self.config(),
                PaperTradingCycleRequest(
                    cycle_id="cycle-1",
                    evaluated_at=datetime.fromisoformat(
                        "2026-08-07T10:05:00+08:00"
                    ),
                    financial_report=self.confirmed_report,
                    quote=self.quote(self.confirmed_report),
                    confirmation_actor="student",
                    confirmation_note="accepted for local simulation",
                ),
            ).to_mapping()
            persisted = store.load()

        self.assertEqual(result["status"], "simulated_fill")
        order = result["cycle"]["simulated_order"]
        self.assertEqual(order["broker"], "local_simulator")
        self.assertFalse(order["real_order"])
        self.assertGreater(order["quantity"], 0)
        self.assertFalse(result["cycle"]["safety"]["order_sent_to_broker"])
        self.assertEqual(len(persisted["cycles"]), 1)
        self.assertEqual(persisted["confirmations"][0]["decision"], "approved")
        self.assertGreater(
            persisted["account"]["positions"]["sz000001"]["shares"], 0
        )

    def test_missing_human_confirmation_is_recorded_without_a_fill(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = PaperTradingRuntime(
                store=JsonPaperTradingLedger(Path(temp_dir) / "ledger.json")
            )
            result = runtime.run_cycle(
                self.config(),
                PaperTradingCycleRequest(
                    cycle_id="cycle-pending",
                    evaluated_at=datetime.fromisoformat(
                        "2026-08-07T10:05:00+08:00"
                    ),
                    financial_report=self.pending_report,
                    quote=self.quote(self.pending_report),
                ),
            ).to_mapping()

        self.assertEqual(result["status"], "pending_human_confirmation")
        self.assertIsNone(result["cycle"]["simulated_order"])
        self.assertEqual(result["review"]["pending_confirmation_count"], 1)
        self.assertEqual(result["review"]["confirmation_record_count"], 1)

    def test_rejected_c3_report_is_persisted_as_a_failure(self):
        report = copy.deepcopy(self.confirmed_report)
        report["real_trading_allowed"] = True
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JsonPaperTradingLedger(Path(temp_dir) / "ledger.json")
            runtime = PaperTradingRuntime(store=store)
            with self.assertRaises(PaperTradingError):
                runtime.run_cycle(
                    self.config(),
                    PaperTradingCycleRequest(
                        cycle_id="bad-cycle",
                        evaluated_at=datetime.fromisoformat(
                            "2026-08-07T10:05:00+08:00"
                        ),
                        financial_report=report,
                        quote=self.quote(report),
                    ),
                )
            persisted = store.load()
            recovery = runtime.record_failure_recovery(
                cycle_id="bad-cycle",
                recovered_at="2026-08-07T10:10:00+08:00",
                note="corrected the safety field before retry",
            )

        self.assertEqual(len(persisted["failures"]), 1)
        self.assertIn("safety field", persisted["failures"][0]["message"])
        self.assertTrue(recovery["recovered"])

    def test_offline_cycle_does_not_fake_the_one_week_live_requirement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = PaperTradingRuntime(
                store=JsonPaperTradingLedger(Path(temp_dir) / "ledger.json")
            )
            result = runtime.run_cycle(
                self.config(),
                PaperTradingCycleRequest(
                    cycle_id="offline-cycle",
                    evaluated_at=datetime.fromisoformat(
                        "2026-08-07T10:05:00+08:00"
                    ),
                    financial_report=self.confirmed_report,
                    quote=self.quote(self.confirmed_report),
                ),
            ).to_mapping()

        self.assertEqual(result["review"]["live_trading_day_count"], 0)
        self.assertFalse(result["review"]["duration_requirement_met"])
        self.assertEqual(result["review"]["formal_task_status"], "in_progress")

    def test_future_execution_quote_is_rejected_and_recorded(self):
        future_quote = PaperTradingQuote(
            symbol="sz000001",
            price=Decimal("11.27"),
            source="tencent.qt.gtimg.cn",
            timestamp=datetime.fromisoformat("2026-08-07T14:30:00+08:00"),
            as_of=datetime.fromisoformat("2026-08-07T15:00:00+08:00"),
            mode="offline",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JsonPaperTradingLedger(Path(temp_dir) / "ledger.json")
            runtime = PaperTradingRuntime(store=store)
            with self.assertRaises(PaperTradingError):
                runtime.run_cycle(
                    self.config(),
                    PaperTradingCycleRequest(
                        cycle_id="future-quote",
                        evaluated_at=datetime.fromisoformat(
                            "2026-08-07T14:30:00+08:00"
                        ),
                        financial_report=self.confirmed_report,
                        quote=future_quote,
                    ),
                )
            ledger = store.load()

        self.assertEqual(len(ledger["failures"]), 1)
        self.assertIn("future", ledger["failures"][0]["message"])

    def test_session_configuration_enforces_a_seven_to_fourteen_day_plan(self):
        started_at = datetime.fromisoformat("2026-08-07T09:30:00+08:00")
        with self.assertRaises(PaperTradingError):
            PaperTradingSessionConfig(
                session_id="too-short",
                symbols=("sz000001",),
                initial_cash=Decimal("100000"),
                started_at=started_at,
                planned_end_at=started_at + timedelta(days=3),
            )


if __name__ == "__main__":
    unittest.main()
