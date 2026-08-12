import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.dashboard import (  # noqa: E402
    ACTION_BY_ID,
    CommandExecution,
    DashboardError,
    DashboardRuntime,
    DeepSeekProjectAssistant,
    LocalProjectAssistant,
    create_server,
)
from agent_platform.client_app import LocalMarketAssistant  # noqa: E402


class _FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, command, *, cwd, timeout_seconds):
        self.calls.append((tuple(command), cwd, timeout_seconds))
        return CommandExecution(
            returncode=0,
            stdout="=== demo ===\nmode: offline\nstatus: succeeded\nreal_trading_allowed=false\n",
            stderr="",
            duration_ms=125,
        )


class _FakeGateway:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            response=SimpleNamespace(
                structured_output={
                    "answer": "当前结果来自确定性程序。",
                    "suggested_action_id": "c3_graph",
                    "reason": "可以继续查看完整决策链。",
                },
                provider="deepseek",
                model="deepseek-test",
                usage=SimpleNamespace(input_tokens=20, output_tokens=12, total_tokens=32),
                latency_ms=88,
            )
        )


class DashboardRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runner = _FakeRunner()
        self.runtime = DashboardRuntime.from_project(
            PROJECT_ROOT,
            command_runner=self.runner,
            assistant=LocalProjectAssistant(),
            market_assistant=LocalMarketAssistant(),
        )

    def test_overview_unifies_every_stage_and_keeps_trading_disabled(self):
        overview = self.runtime.overview()

        self.assertEqual([stage["id"] for stage in overview["stages"]], ["A", "B", "C", "D"])
        self.assertGreaterEqual(len(overview["actions"]), 18)
        self.assertEqual({item["stage"] for item in overview["actions"]}, {"A", "B", "C", "D"})
        self.assertFalse(overview["safety"]["real_trading_allowed"])
        self.assertFalse(overview["safety"]["order_created"])

    def test_client_overview_exposes_twenty_stocks_and_mode_availability(self):
        overview = self.runtime.client_overview()

        self.assertEqual(len(overview["securities"]), 20)
        self.assertEqual(
            next(item for item in overview["securities"] if item["symbol"] == "sz000001")["modes"],
            ["offline", "live"],
        )
        self.assertEqual(
            next(item for item in overview["securities"] if item["symbol"] == "sh600000")["modes"],
            ["live"],
        )

    def test_run_action_uses_only_allowlisted_script_and_extracts_summary(self):
        result = self.runtime.run_action("a4_model")

        command, cwd, timeout_seconds = self.runner.calls[0]
        self.assertEqual(Path(command[1]).name, "demo_model_gateway.py")
        self.assertEqual(command[2:], ())
        self.assertEqual(cwd, PROJECT_ROOT)
        self.assertEqual(timeout_seconds, 180.0)
        self.assertEqual(result["status"], "succeeded")
        self.assertIn("status: succeeded", result["summary"])
        self.assertFalse(result["safety"]["real_trading_allowed"])

    def test_live_mode_adds_only_registered_deepseek_arguments(self):
        self.runtime.run_action("a4_model", mode="live")

        command = self.runner.calls[0][0]
        self.assertEqual(command[2:], ("--live", "--provider", "deepseek"))

    def test_unknown_action_and_unsupported_live_mode_are_rejected(self):
        with self.assertRaises(DashboardError):
            self.runtime.run_action("run-any-command")
        with self.assertRaises(DashboardError):
            self.runtime.run_action("d1_backtest", mode="live")
        self.assertEqual(self.runner.calls, [])

    def test_local_assistant_suggests_but_does_not_execute(self):
        answer = self.runtime.ask_assistant("我想看回测和最大回撤")

        self.assertEqual(answer["suggested_action_id"], "d1_backtest")
        self.assertEqual(answer["provider"], "local")
        self.assertEqual(self.runner.calls, [])

    def test_deepseek_assistant_uses_structured_gateway_and_only_returns_suggestion(self):
        gateway = _FakeGateway()
        assistant = DeepSeekProjectAssistant(gateway, model="deepseek-test")

        answer = assistant.answer(
            "解释当前结果",
            {"action_id": "b1_data", "title": "B1", "summary": "market.daily ok"},
        )

        request = gateway.requests[0]
        self.assertEqual(request.schema_name, "dashboard_assistant")
        self.assertIn("不能声称已经下单", request.system_prompt)
        self.assertEqual(answer["suggested_action_id"], "c3_graph")
        self.assertEqual(answer["usage"]["total_tokens"], 32)
        self.assertEqual(self.runner.calls, [])

    def test_every_registered_script_exists(self):
        for action in ACTION_BY_ID.values():
            with self.subTest(action=action.id):
                self.assertTrue((PROJECT_ROOT / "Scripts" / action.script).is_file())


class DashboardHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = DashboardRuntime.from_project(
            PROJECT_ROOT,
            command_runner=_FakeRunner(),
            assistant=LocalProjectAssistant(),
            market_assistant=LocalMarketAssistant(),
        )
        cls.server = create_server(port=0, runtime=runtime)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_serves_customer_frontend_admin_and_both_overview_apis(self):
        with urlopen(f"{self.base_url}/", timeout=2) as response:
            client_html = response.read().decode("utf-8")
        with urlopen(f"{self.base_url}/admin", timeout=2) as response:
            admin_html = response.read().decode("utf-8")
        with urlopen(f"{self.base_url}/api/overview", timeout=2) as response:
            overview = json.loads(response.read().decode("utf-8"))
        with urlopen(f"{self.base_url}/api/client/overview", timeout=2) as response:
            client_overview = json.loads(response.read().decode("utf-8"))

        self.assertIn("看懂一只股票", client_html)
        self.assertNotIn("Harness", client_html)
        self.assertIn("把 Agent 的能力", admin_html)
        self.assertIn("DeepSeek 助手", admin_html)
        self.assertEqual(len(overview["stages"]), 4)
        self.assertIn("K 线与技术指标", client_overview["capabilities"])

    def test_customer_analysis_and_explanation_are_visible_through_http(self):
        analyze = Request(
            f"{self.base_url}/api/client/analyze",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"symbol": "sz000001", "mode": "offline"}).encode("utf-8"),
        )
        with urlopen(analyze, timeout=20) as response:
            analysis = json.loads(response.read().decode("utf-8"))
        explain = Request(
            f"{self.base_url}/api/client/explain",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"analysis": analysis}).encode("utf-8"),
        )
        with urlopen(explain, timeout=5) as response:
            explanation = json.loads(response.read().decode("utf-8"))

        self.assertEqual(len(analysis["dimensions"]), 4)
        self.assertEqual(len(analysis["data"]["bars"]), 30)
        self.assertEqual(explanation["provider"], "local")

    def test_run_api_rejects_unregistered_commands(self):
        request = Request(
            f"{self.base_url}/api/run",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"action_id": "../../evil", "mode": "offline"}).encode("utf-8"),
        )

        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=2)

        self.assertEqual(raised.exception.code, 400)


class DashboardAssetTests(unittest.TestCase):
    def test_frontend_assets_are_present_and_do_not_use_external_dependencies(self):
        web_root = PROJECT_ROOT / "src" / "agent_platform" / "web"
        html = (web_root / "index.html").read_text(encoding="utf-8")
        client_html = (web_root / "client.html").read_text(encoding="utf-8")
        css = (web_root / "styles.css").read_text(encoding="utf-8")
        client_css = (web_root / "client.css").read_text(encoding="utf-8")
        javascript = (web_root / "app.js").read_text(encoding="utf-8")
        client_javascript = (web_root / "client.js").read_text(encoding="utf-8")

        self.assertIn("/styles.css", html)
        self.assertIn("/app.js", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("https://", client_html)
        self.assertIn("@media (max-width: 570px)", css)
        self.assertIn("@media (max-width: 680px)", client_css)
        self.assertIn("[hidden] { display: none !important; }", client_css)
        self.assertIn('api("/api/overview")', javascript)
        self.assertIn('clientApi("/api/client/overview")', client_javascript)
        self.assertIn("syncModeAvailability", client_javascript)


if __name__ == "__main__":
    unittest.main()
