import json
import sys
import threading
import time
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
        self.assertGreaterEqual(len(overview["actions"]), 19)
        self.assertIn("c1_debate_eval", {item["id"] for item in overview["actions"]})
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

        self.runner.calls.clear()
        self.runtime.run_action("c1_debate_eval", mode="live")
        command = self.runner.calls[0][0]
        self.assertEqual(command[2:], ("--live", "--no-key-prompt"))

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
            f"{self.base_url}/api/client/jobs",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"symbol": "sz000001", "mode": "offline"}).encode("utf-8"),
        )
        with urlopen(analyze, timeout=5) as response:
            job = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 202)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with urlopen(f"{self.base_url}/api/client/jobs/{job['job_id']}", timeout=5) as response:
                status = json.loads(response.read().decode("utf-8"))
            if status["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        self.assertEqual(status["status"], "succeeded", status)
        self.assertEqual(status["progress"]["percent"], 100)
        self.assertEqual(status["progress"]["total"], 17)
        self.assertEqual(
            {stage["id"] for stage in status["progress"]["stages"] if stage["status"] == "completed"},
            {"c1_research", "planner", "technical", "fundamental", "industry", "macro", "aggregate", "c1_debate", "c1_quality", "c1_synthesis", "trader", "market_route", "risk_manager", "finalize", "chart", "report"},
        )
        self.assertEqual(
            next(stage for stage in status["progress"]["stages"] if stage["id"] == "market_bearish_skip")["status"],
            "skipped",
        )
        with urlopen(f"{self.base_url}/api/client/jobs/{job['job_id']}/result", timeout=5) as response:
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
        self.assertEqual(len(analysis["analysis_id"]), 32)
        self.assertEqual(explanation["provider"], "local")

        debate = Request(
            f"{self.base_url}/api/client/debate",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"analysis_id": analysis["analysis_id"]}).encode("utf-8"),
        )
        with urlopen(debate, timeout=5) as response:
            dynamic = json.loads(response.read().decode("utf-8"))

        self.assertEqual(dynamic["mode"], "deterministic_fallback")
        self.assertFalse(dynamic["safety"]["real_trading_allowed"])

    def test_customer_dynamic_debate_rejects_unknown_analysis_id(self):
        request = Request(
            f"{self.base_url}/api/client/debate",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"analysis_id": "not-a-real-analysis"}).encode("utf-8"),
        )

        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=2)

        self.assertEqual(raised.exception.code, 400)

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
        self.assertIn('id="research-balance"', client_html)
        self.assertIn('id="balance-chart"', client_html)
        self.assertIn('id="dynamic-debate-button"', client_html)
        self.assertIn('id="dynamic-debate-rounds"', client_html)
        self.assertIn('id="job-progress"', client_html)
        self.assertIn('id="cancel-analysis-button"', client_html)
        self.assertIn('id="retry-job-button"', client_html)
        self.assertIn(".balance-zero-line", client_css)
        self.assertIn('api("/api/overview")', javascript)
        self.assertIn('clientApi("/api/client/overview")', client_javascript)
        self.assertIn("syncModeAvailability", client_javascript)
        self.assertIn("renderResearchBalance", client_javascript)
        self.assertIn("localizeDebateText", client_javascript)
        self.assertIn('clientApi("/api/client/debate"', client_javascript)
        self.assertIn('clientApi("/api/client/jobs"', client_javascript)
        self.assertIn("followAnalysisJob", client_javascript)
        self.assertIn("retryAnalysisJob", client_javascript)
        self.assertIn("只重试失败步骤", client_html)
        self.assertIn(".job-stage", client_css)


if __name__ == "__main__":
    unittest.main()
