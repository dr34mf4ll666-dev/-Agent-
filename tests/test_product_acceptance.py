import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.client_app import ClientAnalysisRequest, ClientAnalysisRuntime  # noqa: E402
from agent_platform.product_acceptance import ProductAcceptanceRuntime  # noqa: E402


class _FakeFinalDelivery:
    passed = True

    def run(self):
        return SimpleNamespace(
            passed=True,
            to_mapping=lambda: {
                "status": "final_delivery_completed",
                "workflows": [{"passed": True}] * 5,
                "safety": {"real_trading_allowed": False},
            },
        )


class _FixedClientAnalysis:
    def __init__(self, value):
        self.value = value
        self.requests = []

    def analyze(self, request):
        self.requests.append(request)
        return SimpleNamespace(to_mapping=lambda: self.value)


class ProductAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_value = ClientAnalysisRuntime.from_project(PROJECT_ROOT).analyze(
            ClientAnalysisRequest()
        ).to_mapping()

    def test_one_interface_accepts_core_customer_admin_and_safety(self):
        client = _FixedClientAnalysis(self.client_value)
        report = ProductAcceptanceRuntime(
            project_root=PROJECT_ROOT,
            final_delivery=_FakeFinalDelivery(),
            client_analysis=client,
        ).run()
        value = report.to_mapping()

        self.assertTrue(report.passed)
        self.assertTrue(value["core_delivery"]["passed"])
        self.assertTrue(value["client_app"]["passed"])
        self.assertTrue(value["client_app"]["checks"]["客户股票池不少于20只"])
        self.assertTrue(value["client_app"]["checks"]["客户股票池覆盖沪深两市"])
        self.assertTrue(value["client_app"]["checks"]["四维观点已有直观图形"])
        self.assertTrue(value["client_app"]["checks"]["受约束动态多空辩论入口存在"])
        self.assertTrue(
            value["client_app"]["checks"]["异步分析任务和客户进度入口存在"]
        )
        self.assertTrue(
            value["client_app"]["checks"]["统一数据快照和来源健康可见"]
        )
        self.assertTrue(
            value["client_app"]["checks"]["普通版专业版和证据下钻入口存在"]
        )
        self.assertTrue(
            value["client_app"]["checks"]["实际耗时和可操作错误入口存在"]
        )
        self.assertTrue(value["admin_console"]["passed"])
        self.assertTrue(
            value["admin_console"]["checks"]["动态辩论固定评测入口存在"]
        )
        self.assertTrue(
            value["admin_console"]["checks"]["统一追踪和可靠性瀑布入口存在"]
        )
        self.assertGreaterEqual(value["admin_console"]["action_count"], 19)
        self.assertTrue(value["model_assistance"]["passed"])
        self.assertTrue(
            value["model_assistance"]["checks"]["启动命令支持隐藏输入DeepSeek Key"]
        )
        self.assertTrue(value["safety"]["passed"])
        self.assertEqual(value["admin_console"]["path"], "/admin")
        self.assertIsInstance(client.requests[0], ClientAnalysisRequest)

    def test_customer_surface_failure_fails_complete_acceptance(self):
        broken = dict(self.client_value)
        broken["dimensions"] = broken["dimensions"][:3]
        report = ProductAcceptanceRuntime(
            project_root=PROJECT_ROOT,
            final_delivery=_FakeFinalDelivery(),
            client_analysis=_FixedClientAnalysis(broken),
        ).run()

        self.assertFalse(report.passed)
        self.assertFalse(report.client_app["checks"]["四个研究维度齐全"])


if __name__ == "__main__":
    unittest.main()
