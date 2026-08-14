import sys
import unittest
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.report_views import ReportViewError, ReportViewRuntime  # noqa: E402


class _Repository:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def get_report(self, report_id):
        self.calls.append(report_id)
        return deepcopy(self.value)


def _archive():
    bars = [
        {"date": f"2026-08-{day:02d}", "open": str(day), "high": str(day + 1),
         "low": str(day - 1), "close": str(day + 0.5), "volume": str(day * 1000)}
        for day in range(1, 22)
    ]
    dimensions = [
        {"id": "technical", "name": "趋势走势", "score": 10, "label": "中性", "summary": "技术摘要"},
        {"id": "fundamental", "name": "经营质量", "score": 60, "label": "偏强", "summary": "经营摘要"},
        {"id": "industry", "name": "行业温度", "score": 20, "label": "中性", "summary": "行业摘要"},
        {"id": "macro", "name": "市场环境", "score": -15, "label": "谨慎", "summary": "宏观摘要"},
    ]
    base_agent = {"as_of": "2026-08-06T15:00:00+08:00", "timestamp": "2026-08-07T10:00:00+08:00", "sources": ["fixture"], "caveats": []}
    agents = {
        "technical": {**base_agent, "ma": {"sma_5": "10", "sma_10": "9", "sma_20": "8"}, "macd": {"dif": "1", "dea": "0.5"}, "rsi": {"rsi_14": "55"}, "kdj": {"k": "60"}, "bollinger": {"upper": "12"}, "levels": {"support_20": "8", "resistance_20": "12"}},
        "fundamental": {**base_agent, "valuation": {"pe_dynamic": "7", "pb": "0.7", "ps": "2"}, "indicators": {"weighted_roe_percent": "12"}, "growth": {"net_profit_growth_percent": "5"}, "dcf": {"margin_of_safety_percent": "20"}},
        "industry": {**base_agent, "prosperity": {"sector_change_percent": "1", "label": "improving"}, "policy": {"lpr_1y": "3"}, "industry_profile": {"company_count": 40}},
        "macro": {**base_agent, "index": {"window_return_percent": "1"}, "funds": {"net_flow_cny": "100"}, "macro": {"gdp_current_percent": "5", "shibor_1w": "1.4"}, "market_regime": {"label": "mixed"}, "risk_appetite": {"label": "moderate"}},
    }
    result = {
        "security": {"symbol": "sz000001", "name": "平安银行", "code": "000001", "exchange": "深交所"},
        "data": {"mode": "offline", "label": "已验证历史快照", "as_of": "2026-08-06T15:00:00+08:00", "timestamp": "2026-08-07T10:00:00+08:00", "source_count": 4, "snapshot_id": "s" * 32, "sources": ["fixture"], "bars": bars},
        "quote": {"latest_close": "11", "daily_return_percent": "1", "support": "8", "resistance": "12"},
        "verdict": {"label": "谨慎偏强", "action": "buy", "action_label": "偏多关注", "confidence": 69, "weighted_score": "24.75"},
        "price_band": {"lower": "9", "reference": "11", "upper": "13", "note": "研究区间"},
        "risk": {"position_cap_percent": "15", "estimated_loss_percent": "1", "reward_risk_ratio": "2", "status": "通过"},
        "safety": {"notice": "不构成投资建议", "simulation_only": True, "order_created": False, "real_trading_allowed": False},
        "dimensions": dimensions,
        "debate": {"positive_reasoning": "多方证据", "risk_reasoning": "空方证据", "positive": "多方", "risk": "空方", "rounds": 2},
        "quality": {"consistency": "passed", "bias": "passed"},
    }
    stages = [
        {"id": item, "status": "completed", "label": item, "group": "x", "attempts": 1}
        for item in ("c1_research", "planner", "technical", "fundamental", "industry", "macro", "aggregate", "c1_debate", "c1_quality", "c1_synthesis", "trader", "market_route", "risk_manager", "finalize", "chart", "report")
    ]
    stages.append({"id": "market_bearish_skip", "status": "skipped", "label": "skip", "group": "risk", "attempts": 0})
    return {"report_id": "r" * 32, "report_version": 1, "archived_at": "2026-08-07T10:00:00+08:00", "result": result, "agents": agents, "task": {"stages": stages}, "snapshot": {"snapshot_id": "s" * 32, "datasets": [{"dataset": "market.daily", "status": "primary", "records": [{"secret": "raw"}]}]}}


class ReportViewRuntimeTests(unittest.TestCase):
    def test_basic_projection_hides_professional_details_and_groups_progress(self):
        repository = _Repository(_archive())
        value = ReportViewRuntime(repository).project("r" * 32, "basic")

        self.assertEqual(value["view"], "basic")
        self.assertNotIn("professional", value)
        self.assertEqual([item["status"] for item in value["basic"]["stages"]], ["completed"] * 4)
        self.assertEqual(value["basic"]["support"]["title"], "经营质量")
        self.assertEqual(value["basic"]["risk"]["title"], "市场环境")
        self.assertEqual(
            [item["label"] for item in value["basic"]["guide"]],
            ["结论摘要", "主要依据", "主要风险", "关注区间"],
        )
        self.assertEqual(value["basic"]["headline"], "谨慎关注")
        guide_text = " ".join(
            f"{item['answer']} {item['detail']}" for item in value["basic"]["guide"]
        )
        for jargon in ("SMA", "PE", "规则分数", "风险收益比", "？", "先看"):
            self.assertNotIn(jargon, guide_text)
        self.assertIn("9", value["basic"]["price_explanation"])
        self.assertIn("13", value["basic"]["price_explanation"])
        self.assertEqual(repository.calls, ["r" * 32])

    def test_professional_projection_exposes_nodes_metrics_sources_and_chart_series(self):
        value = ReportViewRuntime(_Repository(_archive())).project("r" * 32, "professional")
        professional = value["professional"]

        self.assertEqual(len(professional["task_nodes"]), 17)
        self.assertEqual(len(professional["agent_details"]), 4)
        self.assertEqual(professional["agent_details"][0]["metrics"][0]["label"], "SMA5")
        self.assertEqual(len(professional["evidence_index"]), 4)
        chart = value["shared"]["chart"]
        self.assertEqual(len(chart["series"]["daily"]["indicators"]["sma5"]), 21)
        self.assertIsNone(chart["series"]["daily"]["indicators"]["sma20"][18])
        self.assertIsNotNone(chart["series"]["daily"]["indicators"]["sma20"][19])
        self.assertEqual(chart["periods"][1], {"id": "weekly", "label": "周 K"})
        self.assertLess(len(chart["series"]["weekly"]["bars"]), 21)
        self.assertNotIn("records", professional["snapshot"]["datasets"][0])

    def test_both_views_keep_identical_core_facts_and_do_not_mutate_archive(self):
        archive = _archive()
        original = deepcopy(archive)
        runtime = ReportViewRuntime(_Repository(archive))

        basic = runtime.project("r" * 32, "basic")
        professional = runtime.project("r" * 32, "professional")

        self.assertEqual(basic["shared"], professional["shared"])
        self.assertEqual(basic["projection_fingerprint"], professional["projection_fingerprint"])
        self.assertEqual(archive, original)

    def test_unknown_view_is_rejected_before_repository_read(self):
        repository = _Repository(_archive())
        with self.assertRaisesRegex(ReportViewError, "basic 或 professional"):
            ReportViewRuntime(repository).project("r" * 32, "admin")
        self.assertEqual(repository.calls, [])


if __name__ == "__main__":
    unittest.main()
