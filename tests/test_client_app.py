import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.client_app import (  # noqa: E402
    ClientAnalysisError,
    ClientAnalysisRequest,
    ClientAnalysisRuntime,
    DeepSeekMarketAssistant,
    LocalMarketAssistant,
    SECURITIES,
)


class _FakeGateway:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            response=SimpleNamespace(
                structured_output={
                    "headline": "四维观点存在分化",
                    "explanation": "经营质量较强，市场环境偏弱。",
                    "risk_note": "研究区间不是收益承诺。",
                },
                provider="deepseek",
                model="deepseek-test",
                usage=SimpleNamespace(input_tokens=30, output_tokens=20, total_tokens=50),
                latency_ms=90,
            )
        )


class _CapturingGraph:
    def __init__(self):
        self.query = None

    def run(self, query):
        self.query = query
        raise RuntimeError("stop after query capture")


class ClientAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = ClientAnalysisRuntime.from_project(PROJECT_ROOT).analyze(
            ClientAnalysisRequest()
        ).to_mapping()

    def test_offline_customer_projection_contains_chart_dimensions_and_safe_verdict(self):
        value = self.analysis

        self.assertEqual(value["security"]["name"], "平安银行")
        self.assertEqual(len(value["data"]["bars"]), 30)
        self.assertEqual({item["id"] for item in value["dimensions"]}, {"technical", "fundamental", "industry", "macro"})
        self.assertEqual(value["verdict"]["label"], "谨慎偏强")
        self.assertFalse(value["safety"]["real_trading_allowed"])
        self.assertFalse(value["safety"]["order_created"])
        self.assertEqual(len(value["data"]["snapshot_id"]), 32)
        self.assertEqual(value["data"]["snapshot"]["dataset_count"], 14)
        self.assertEqual(value["data"]["snapshot"]["available_count"], 14)
        self.assertFalse(value["data"]["snapshot"]["degraded"])
        daily = next(
            item
            for item in value["data"]["snapshot"]["datasets"]
            if item["dataset"] == "market.daily"
        )
        self.assertEqual(len(value["data"]["bars"]), 30)
        self.assertEqual(daily["status"], "fixture")

    def test_customer_runtime_reports_only_real_analysis_phase_transitions(self):
        events = []

        ClientAnalysisRuntime.from_project(PROJECT_ROOT).analyze(
            ClientAnalysisRequest(),
            progress=lambda stage, status: events.append((stage, status)),
        )

        self.assertEqual(
            [event for event in events if event[0] in {"research", "chart", "report"}],
            [
                ("research", "running"),
                ("research", "completed"),
                ("chart", "running"),
                ("chart", "completed"),
                ("report", "running"),
                ("report", "completed"),
            ],
        )

    def test_customer_request_rejects_unavailable_symbol_and_mode(self):
        with self.assertRaises(ClientAnalysisError):
            ClientAnalysisRequest(symbol="sz999999")
        with self.assertRaises(ClientAnalysisError):
            ClientAnalysisRequest(mode="unknown")

    def test_customer_catalog_contains_twenty_verified_bank_symbols(self):
        self.assertEqual(len(SECURITIES), 20)
        self.assertEqual(
            {item["exchange"] for item in SECURITIES.values()},
            {"上交所", "深交所"},
        )
        self.assertEqual(set(SECURITIES["sz000001"]["sectors"]), {"offline", "live"})
        self.assertEqual(set(SECURITIES["sh600000"]["sectors"]), {"live"})

    def test_non_ping_an_symbols_do_not_reuse_its_offline_fixture(self):
        with self.assertRaisesRegex(ClientAnalysisError, "只支持最新数据"):
            ClientAnalysisRequest(symbol="sh600000", mode="offline")

    def test_live_customer_query_uses_provider_sector_name(self):
        graph = _CapturingGraph()
        runtime = ClientAnalysisRuntime(
            graph=graph,
            market_tool=object(),
            now=lambda: datetime(2026, 8, 11, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

        with self.assertRaisesRegex(RuntimeError, "query capture"):
            runtime.analyze(ClientAnalysisRequest(mode="live"))

        self.assertEqual(
            graph.query.c1_query.combined_query.industry.sector,
            "金融行业",
        )

    def test_local_explanation_uses_computed_strength_and_risk_boundary(self):
        result = LocalMarketAssistant().explain(self.analysis)

        self.assertIn("经营质量", result["explanation"])
        self.assertIn(self.analysis["price_band"]["lower"], result["risk_note"])
        self.assertEqual(result["provider"], "local")

    def test_deepseek_explanation_uses_schema_and_cannot_change_analysis(self):
        gateway = _FakeGateway()
        before = dict(self.analysis["verdict"])

        result = DeepSeekMarketAssistant(gateway, model="deepseek-test").explain(self.analysis)

        self.assertEqual(gateway.requests[0].schema_name, "client_market_explanation")
        self.assertIn("不能改写分数", gateway.requests[0].system_prompt)
        self.assertEqual(result["usage"]["total_tokens"], 50)
        self.assertEqual(self.analysis["verdict"], before)


if __name__ == "__main__":
    unittest.main()
