import copy
import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    C1DecisionQuery,
    C2TradingQuery,
    CombinedAnalysisQuery,
    RiskContext,
    RiskManagerQuery,
    TraderQuery,
    build_default_c1_decision_runtime,
    build_default_c2_trading_runtime,
    build_default_risk_manager_runtime,
    build_default_trader_runtime,
    validate_risk_review,
)


class RiskManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c1 = build_default_c1_decision_runtime(project_root=PROJECT_ROOT).run(
            C1DecisionQuery(CombinedAnalysisQuery.for_symbol())
        ).to_mapping()["report"]
        cls.trader_runtime = build_default_trader_runtime()
        cls.trader = cls.trader_runtime.run(TraderQuery(cls.c1)).to_mapping()["report"]
        cls.risk_runtime = build_default_risk_manager_runtime()
        cls.c2_runtime = build_default_c2_trading_runtime()

    def context(self, **overrides):
        values = {
            "account_equity": "100000",
            "current_position_percent": "0",
            "requested_position_percent": "15",
            "sector_exposure_other_percent": "5",
            "current_drawdown_percent": "5",
            "average_daily_turnover": "500000000",
            "evaluation_time": "2026-08-07T10:00:00+08:00",
            "stop_loss_price": "10.49",
            "take_profit_price": "13.02",
            "human_confirmed": True,
        }
        values.update(overrides)
        return RiskContext(**values)

    def run_risk(self, context):
        return self.risk_runtime.run(
            RiskManagerQuery(
                c1_decision=self.c1,
                trader_candidate=self.trader,
                risk_context=context,
            )
        ).to_mapping()

    def test_approved_buy_enforces_all_limits_without_creating_order(self):
        result = self.run_risk(self.context())
        report = result["report"]

        self.assertEqual(report["risk_decision"]["status"], "approved")
        self.assertEqual(report["risk_decision"]["approved_action"], "buy")
        self.assertEqual(report["position"]["approved_percent"], "15.00")
        self.assertEqual(report["position"]["final_sector_exposure_percent"], "20.00")
        self.assertLessEqual(
            Decimal(report["position"]["estimated_single_trade_loss_percent"]),
            Decimal("2"),
        )
        self.assertTrue(report["execution"]["simulation_execution_allowed"])
        self.assertFalse(report["execution"]["order_created"])
        self.assertFalse(report["execution"]["real_trading_allowed"])
        self.assertIn(
            "risk_manager.completed",
            [event["event"] for event in result["trace"]],
        )
        self.assertIn(
            "guardrail.output.passed",
            [event["event"] for event in result["harness_trace"]],
        )
        self.assertIn(
            "risk_preflight",
            [
                event["detail"]
                for event in result["harness_trace"]
                if event["event"] == "guardrail.input.passed"
            ],
        )

    def test_position_above_ten_requires_explicit_human_confirmation(self):
        report = self.run_risk(self.context(human_confirmed=False))["report"]

        self.assertEqual(
            report["risk_decision"]["status"],
            "pending_human_confirmation",
        )
        self.assertTrue(report["execution"]["human_confirmation_required"])
        self.assertFalse(report["execution"]["simulation_execution_allowed"])

    def test_single_trade_loss_rule_reduces_requested_position(self):
        report = self.run_risk(
            self.context(stop_loss_price="9.00", take_profit_price="15.00")
        )["report"]

        self.assertEqual(report["risk_decision"]["status"], "adjusted")
        self.assertLess(
            Decimal(report["position"]["approved_percent"]), Decimal("15")
        )
        self.assertLessEqual(
            Decimal(report["position"]["estimated_single_trade_loss_percent"]),
            Decimal("2"),
        )

    def test_sector_exposure_rule_caps_final_sector_at_thirty(self):
        report = self.run_risk(
            self.context(sector_exposure_other_percent="28")
        )["report"]

        self.assertEqual(report["risk_decision"]["status"], "adjusted")
        self.assertEqual(report["position"]["approved_percent"], "2.00")
        self.assertEqual(report["position"]["final_sector_exposure_percent"], "30.00")

    def test_drawdown_above_fifteen_forces_half_position_reduction(self):
        report = self.run_risk(
            self.context(
                current_position_percent="20",
                requested_position_percent="20",
                current_drawdown_percent="16",
            )
        )["report"]

        self.assertEqual(report["risk_decision"]["status"], "forced_reduction")
        self.assertEqual(report["risk_decision"]["approved_action"], "reduce")
        self.assertEqual(report["position"]["approved_percent"], "10.00")

    def test_outside_session_low_liquidity_and_invalid_stops_block_buy(self):
        cases = (
            (self.context(evaluation_time="2026-08-07T12:00:00+08:00"), "trading_session"),
            (self.context(average_daily_turnover="1000000"), "liquidity"),
            (self.context(stop_loss_price="11.50"), "stop_loss_take_profit"),
        )
        for context, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                report = self.run_risk(context)["report"]
                self.assertEqual(report["risk_decision"]["status"], "blocked")
                self.assertIn(expected_reason, report["risk_decision"]["reason"])
                self.assertFalse(report["execution"]["simulation_execution_allowed"])

    def test_bearish_market_blocks_new_buy(self):
        c1 = copy.deepcopy(self.c1)
        c1["market_regime_gate"]["regime"] = "bearish"
        c1["synthesis"]["market_regime_gate"]["regime"] = "bearish"
        trader = self.trader_runtime.run(TraderQuery(c1)).to_mapping()["report"]

        report = self.risk_runtime.run(
            RiskManagerQuery(c1, trader, self.context())
        ).to_mapping()["report"]

        self.assertEqual(report["risk_decision"]["status"], "blocked")
        self.assertIn("market_regime", report["risk_decision"]["reason"])

    def test_sell_candidate_can_reduce_position_without_confirmation(self):
        c1 = copy.deepcopy(self.c1)
        c1["synthesis"]["inclination"] = "negative"
        c1["synthesis"]["raw_inclination"] = "negative"
        c1["synthesis"]["weighted_score"] = "-30.00"
        c1["synthesis"]["confidence"] = 70
        trader = self.trader_runtime.run(TraderQuery(c1)).to_mapping()["report"]
        context = self.context(
            current_position_percent="15",
            requested_position_percent="0",
            stop_loss_price=None,
            take_profit_price=None,
            human_confirmed=False,
        )

        report = self.risk_runtime.run(
            RiskManagerQuery(c1, trader, context)
        ).to_mapping()["report"]

        self.assertEqual(trader["signal"]["action"], "sell")
        self.assertEqual(report["risk_decision"]["approved_action"], "sell")
        self.assertEqual(report["position"]["approved_percent"], "0.00")
        self.assertFalse(report["execution"]["human_confirmation_required"])

    def test_validator_rejects_tampered_approved_position(self):
        context = self.context()
        query = RiskManagerQuery(self.c1, self.trader, context)
        report = self.risk_runtime.run(query).to_mapping()["report"]
        tampered = copy.deepcopy(report)
        tampered["position"]["approved_percent"] = "99.00"

        validation = validate_risk_review(tampered, query)

        self.assertFalse(validation.valid)
        self.assertIn("position", validation.detail)

    def test_complete_c2_runtime_and_graph_node_run_trader_then_risk(self):
        query = C2TradingQuery(self.c1, self.context())

        result = self.c2_runtime.run(query).to_mapping()
        graph_result = self.c2_runtime.run_graph_node(
            {
                "c1_decision": self.c1,
                "risk_context": self.context().to_mapping(),
            }
        )

        self.assertEqual(result["report"]["status"], "c2_completed")
        self.assertEqual(
            result["report"]["risk_manager"]["report"]["risk_decision"]["status"],
            "approved",
        )
        self.assertFalse(result["report"]["order_created"])
        self.assertEqual(
            graph_result["c2_trading"]["report"]["status"],
            "c2_completed",
        )


if __name__ == "__main__":
    unittest.main()
