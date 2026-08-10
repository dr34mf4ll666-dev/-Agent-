import copy
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (
    C1DecisionQuery,
    CombinedAnalysisQuery,
    FinancialBatchError,
    FinancialBatchQuery,
    FinancialBatchRuntime,
    FinancialGraphQuery,
    RiskContext,
)


def query(symbol):
    return FinancialGraphQuery(
        c1_query=C1DecisionQuery(
            CombinedAnalysisQuery.for_symbol(symbol=symbol, sector="银行")
        ),
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


class _Result:
    def __init__(self, value):
        self.value = value

    def to_mapping(self):
        return copy.deepcopy(self.value)


class _Runtime:
    def run(self, item):
        symbol = item.c1_query.combined_query.technical.symbol
        specialist = {
            "timestamp": "2026-08-07T16:00:00+08:00",
            "as_of": "2026-08-07T15:00:00+08:00",
            "sources": ["live.test"],
        }
        loops = {
            name: {"harness_trace": [{"event": "postflight.passed"}]}
            for name in ("technical", "fundamental", "industry", "macro")
        }
        return _Result(
            {
                "report": {
                    "symbol": symbol,
                    "mode": "live",
                    "research": {
                        "report": {
                            "synthesis": {
                                "inclination": "positive",
                                "confidence": 80,
                                "target_price_interval": {
                                    "lower": "9",
                                    "reference": "10",
                                    "upper": "12",
                                },
                            },
                            "combined_analysis": {
                                "sources": ["live.test"],
                                "reports": {
                                    name: copy.deepcopy(specialist)
                                    for name in loops
                                },
                                "loops": loops,
                            },
                        },
                        "trace": [],
                    },
                    "trader": {
                        "report": {"signal": {"action": "buy"}},
                        "harness_trace": [],
                    },
                    "route": {"selected_path": "risk_review"},
                    "risk_manager": {
                        "report": {
                            "position": {
                                "approved_percent": "15.00",
                                "estimated_single_trade_loss_percent": "1.00",
                            }
                        },
                        "harness_trace": [],
                    },
                    "final_decision": {
                        "status": "approved",
                        "approved_action": "buy",
                    },
                },
                "graph": {
                    "statuses": {"finalize": "completed"},
                    "execution_order": ["c1_research", "finalize"],
                    "attempts": {"c1_research": 1, "finalize": 1},
                    "trace": [],
                },
            }
        )


class FinancialBatchTests(unittest.TestCase):
    def test_twenty_unique_stocks_produce_three_complete_deliverables(self):
        symbols = [f"sh60{index:04d}" for index in range(20)]
        progress = []
        runtime = FinancialBatchRuntime(
            _Runtime,
            progress_callback=lambda *event: progress.append(event),
        )

        result = runtime.run(FinancialBatchQuery([query(x) for x in symbols]))
        output = result.to_mapping()

        self.assertEqual(output["completed_count"], 20)
        self.assertEqual(output["failed_count"], 0)
        self.assertTrue(output["acceptance_20_met"])
        self.assertEqual(len(output["reports"]), 20)
        self.assertEqual(len(output["trade_advice"]), 20)
        self.assertEqual(len(output["audit_logs"]), 20)
        self.assertEqual(len(progress), 40)
        self.assertTrue(all(not item["real_trading_allowed"] for item in output["trade_advice"]))

    def test_duplicate_symbols_are_rejected(self):
        with self.assertRaises(FinancialBatchError):
            FinancialBatchQuery([query("sz000001"), query("sz000001")])


if __name__ == "__main__":
    unittest.main()
