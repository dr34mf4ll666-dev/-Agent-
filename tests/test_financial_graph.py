import copy
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance.c1_decision import C1DecisionQuery
from agent_platform.finance.combined_analysis import CombinedAnalysisQuery
from agent_platform.finance.financial_graph import (
    FinancialGraphQuery,
    FinancialGraphRuntime,
    build_default_financial_graph_runtime,
    validate_financial_graph_report,
)
from agent_platform.finance.risk_manager import RiskContext
from agent_platform.core import GraphExecutionError, JsonCheckpointStore
from agent_platform.finance.c1_decision import build_default_c1_decision_runtime
from agent_platform.finance.risk_manager import build_default_risk_manager_runtime
from agent_platform.finance.trader import build_default_trader_runtime


class _MappingResult:
    def __init__(self, value):
        self._value = value

    def to_mapping(self):
        return copy.deepcopy(self._value)


class _FixedRuntime:
    def __init__(self, value, *, graph_key=None):
        self._value = value
        self._graph_key = graph_key
        self.calls = 0

    def run(self, query):
        self.calls += 1
        return _MappingResult(self._value)

    def run_graph_node(self, state):
        self.calls += 1
        return {self._graph_key: copy.deepcopy(self._value)}


class _ForbiddenRuntime:
    def __init__(self):
        self.calls = 0

    def run(self, query):
        self.calls += 1
        raise AssertionError("Risk Manager must be skipped on bearish buy route")

    def run_graph_node(self, state):
        return self.run(state)


class _CountingC1Runtime:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0

    def run(self, query):
        self.calls += 1
        return self.delegate.run(query)


class _CountingTraderRuntime:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0

    def run_graph_node(self, state):
        self.calls += 1
        return self.delegate.run_graph_node(state)


class _FailOnceRiskRuntime:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0

    def run_graph_node(self, state):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated transient Risk Manager failure")
        return self.delegate.run_graph_node(state)


class FinancialGraphTests(unittest.TestCase):
    @staticmethod
    def query():
        return FinancialGraphQuery(
            c1_query=C1DecisionQuery(CombinedAnalysisQuery.for_symbol()),
            risk_context=RiskContext(
                account_equity="100000",
                current_position_percent="0",
                requested_position_percent="15",
                sector_exposure_other_percent="5",
                current_drawdown_percent="5",
                average_daily_turnover="500000000",
                evaluation_time="2026-08-07T10:00:00+08:00",
                stop_loss_price="10.49",
                take_profit_price="13.02",
                human_confirmed=True,
            ),
        )

    def test_single_symbol_graph_runs_c1_trader_route_risk_and_finalize(self):
        result = build_default_financial_graph_runtime(
            project_root=PROJECT_ROOT
        ).run(self.query()).to_mapping()

        report = result["report"]
        graph = result["graph"]
        self.assertEqual(report["status"], "financial_graph_completed")
        self.assertEqual(report["symbol"], "sz000001")
        self.assertEqual(report["route"]["selected_path"], "risk_review")
        self.assertEqual(report["decision_source"], "risk_manager")
        self.assertEqual(report["final_decision"]["status"], "approved")
        self.assertFalse(report["order_created"])
        self.assertFalse(report["real_trading_allowed"])
        self.assertEqual(
            graph["execution_order"],
            ["c1_research", "trader", "market_route", "risk_manager", "finalize"],
        )
        self.assertEqual(graph["statuses"]["market_bearish_skip"], "skipped")
        specialist_order = report["research"]["specialist_graph"][
            "execution_order"
        ]
        self.assertIn("planner", specialist_order)
        self.assertIn("aggregate", specialist_order)

    def test_bearish_buy_uses_conditional_skip_without_running_risk_manager(self):
        c1 = {
            "report": {
                "status": "c1_completed",
                "symbol": "sz000001",
                "mode": "offline",
            },
            "specialist_graph": {},
            "trace": [],
        }
        trader = {
            "report": {
                "symbol": "sz000001",
                "mode": "offline",
                "signal": {"action": "buy"},
                "market_context": {"regime": "bearish"},
                "target_price_interval": {
                    "lower": "10.49",
                    "reference": "11.22",
                    "upper": "13.02",
                },
            },
            "harness_trace": [],
            "trace": [],
        }
        c1_runtime = _FixedRuntime(c1)
        trader_runtime = _FixedRuntime(trader, graph_key="trader_candidate")
        risk_runtime = _ForbiddenRuntime()
        runtime = FinancialGraphRuntime(
            c1_runtime=c1_runtime,
            trader_runtime=trader_runtime,
            risk_manager_runtime=risk_runtime,
        )

        result = runtime.run(self.query()).to_mapping()

        report = result["report"]
        self.assertEqual(report["route"]["selected_path"], "skip_bearish_buy")
        self.assertEqual(report["decision_source"], "market_route")
        self.assertEqual(report["final_decision"]["status"], "blocked")
        self.assertEqual(report["final_decision"]["approved_action"], "hold")
        self.assertIsNone(report["risk_manager"])
        self.assertEqual(risk_runtime.calls, 0)
        self.assertEqual(result["graph"]["statuses"]["risk_manager"], "skipped")
        self.assertIn("market_bearish_skip", result["graph"]["execution_order"])

    def test_validator_rejects_tampered_conditional_route(self):
        result = build_default_financial_graph_runtime(
            project_root=PROJECT_ROOT
        ).run(self.query()).to_mapping()["report"]
        result["route"]["selected_path"] = "skip_bearish_buy"

        validation = validate_financial_graph_report(result)

        self.assertFalse(validation.valid)
        self.assertIn("conditional route", validation.detail)

    def test_missing_stops_use_c1_research_interval_without_rerunning_c1(self):
        query = self.query()
        query = FinancialGraphQuery(
            c1_query=query.c1_query,
            risk_context=RiskContext(
                account_equity="100000",
                current_position_percent="0",
                requested_position_percent="15",
                sector_exposure_other_percent="5",
                current_drawdown_percent="5",
                average_daily_turnover="500000000",
                evaluation_time="2026-08-07T10:00:00+08:00",
                human_confirmed=True,
            ),
        )

        result = build_default_financial_graph_runtime(
            project_root=PROJECT_ROOT
        ).run(query).to_mapping()["report"]

        self.assertEqual(result["route"]["stop_loss_source"], "c1_target_lower")
        self.assertEqual(result["route"]["take_profit_source"], "c1_target_upper")
        price_controls = result["risk_manager"]["report"]["price_controls"]
        interval = result["trader"]["report"]["target_price_interval"]
        self.assertEqual(price_controls["stop_loss_price"], interval["lower"])
        self.assertEqual(price_controls["take_profit_price"], interval["upper"])

    def test_checkpoint_resume_retries_only_failed_node(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            c1_runtime = _CountingC1Runtime(
                build_default_c1_decision_runtime(project_root=PROJECT_ROOT)
            )
            trader_runtime = _CountingTraderRuntime(build_default_trader_runtime())
            risk_runtime = _FailOnceRiskRuntime(
                build_default_risk_manager_runtime()
            )
            runtime = FinancialGraphRuntime(
                c1_runtime=c1_runtime,
                trader_runtime=trader_runtime,
                risk_manager_runtime=risk_runtime,
                checkpoint_store=JsonCheckpointStore(
                    Path(temp_dir) / "financial-graph.json"
                ),
            )

            with self.assertRaises(GraphExecutionError) as raised:
                runtime.run(self.query())

            self.assertEqual(raised.exception.statuses["risk_manager"], "failed")
            result = runtime.run(resume=True).to_mapping()

        self.assertEqual(result["report"]["status"], "financial_graph_completed")
        self.assertEqual(c1_runtime.calls, 1)
        self.assertEqual(trader_runtime.calls, 1)
        self.assertEqual(risk_runtime.calls, 2)
        self.assertEqual(result["graph"]["attempts"]["c1_research"], 1)
        self.assertEqual(result["graph"]["attempts"]["trader"], 1)
        self.assertEqual(result["graph"]["attempts"]["risk_manager"], 2)


if __name__ == "__main__":
    unittest.main()
