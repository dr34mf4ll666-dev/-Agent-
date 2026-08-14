import json
import sys
import tempfile
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
from agent_platform.client_app import ClientAnalysisRuntime  # noqa: E402
from agent_platform.analysis_jobs import AnalysisJobRuntime  # noqa: E402
from agent_platform.analysis_repository import InMemoryAnalysisRepository  # noqa: E402
from agent_platform.analysis_observability import (  # noqa: E402
    AnalysisObservabilityRuntime,
    InMemoryAnalysisTraceStore,
)
from agent_platform.report_views import ReportViewRuntime  # noqa: E402
from agent_platform.research_workspace import (  # noqa: E402
    InMemoryResearchWorkspaceStore,
    ResearchWorkspaceRuntime,
)


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
        self.temporary_runtime = tempfile.TemporaryDirectory()
        repository = InMemoryAnalysisRepository()
        observability = AnalysisObservabilityRuntime(InMemoryAnalysisTraceStore())
        client_runtime = ClientAnalysisRuntime.from_project(PROJECT_ROOT)
        analysis_jobs = AnalysisJobRuntime.from_client_runtime(
            client_runtime,
            checkpoint_root=Path(self.temporary_runtime.name) / "checkpoints",
            repository=repository,
            observability=observability,
        )
        self.runtime = DashboardRuntime.from_project(
            PROJECT_ROOT,
            command_runner=self.runner,
            assistant=LocalProjectAssistant(),
            client_runtime=client_runtime,
            market_assistant=LocalMarketAssistant(),
            analysis_jobs=analysis_jobs,
            analysis_repository=repository,
            research_workspace=ResearchWorkspaceRuntime(
                repository,
                ReportViewRuntime(repository),
                InMemoryResearchWorkspaceStore(),
            ),
            observability=observability,
        )

    def tearDown(self):
        self.runtime.analysis_jobs.close()
        self.temporary_runtime.cleanup()

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

    def test_research_workspace_watchlist_is_available_through_dashboard_interface(self):
        initial = self.runtime.get_client_research_workspace()
        added = self.runtime.toggle_client_watchlist("sz000001")

        self.assertEqual(initial["watchlist"], [])
        self.assertTrue(added["added"])
        self.assertEqual(added["workspace"]["watchlist"][0]["name"], "平安银行")

    def test_history_interface_reopens_the_same_frozen_report(self):
        job = self.runtime.submit_client_analysis({"symbol": "sz000001", "mode": "offline"})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = self.runtime.get_client_analysis_job(job["job_id"])
            if status["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.02)
        self.assertEqual(status["status"], "succeeded", status)
        current = self.runtime.get_client_analysis_result(job["job_id"])
        self.runtime.explain_client(current)
        trace = self.runtime.get_observability_trace(job["trace_id"])
        layers = {span["layer"] for span in trace["spans"]}

        history = self.runtime.list_client_analysis_history()
        reopened = self.runtime.get_client_historical_report(current["report_id"])

        self.assertEqual(history["reports"][0]["report_id"], current["report_id"])
        self.assertEqual(reopened["data"]["snapshot_id"], current["data"]["snapshot_id"])
        self.assertEqual(reopened["verdict"], current["verdict"])
        self.assertEqual(reopened["history"]["task_status"], "succeeded")
        self.assertEqual(reopened["history"]["explanation"]["provider"], "local")
        self.assertEqual(current["trace_id"], job["trace_id"])
        self.assertTrue(
            {"http", "task", "data", "graph", "harness", "model", "database"}.issubset(layers)
        )
        self.assertGreaterEqual(
            self.runtime.get_observability_overview()["metrics"]["trace_count"], 1
        )

        basic = self.runtime.get_client_report_view(current["report_id"], view="basic")
        professional = self.runtime.get_client_report_view(
            current["report_id"], view="professional"
        )
        self.assertEqual(basic["shared"], professional["shared"])
        self.assertNotIn("professional", basic)
        self.assertEqual(len(professional["professional"]["task_nodes"]), 17)

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
        cls.temporary_runtime = tempfile.TemporaryDirectory()
        repository = InMemoryAnalysisRepository()
        observability = AnalysisObservabilityRuntime(InMemoryAnalysisTraceStore())
        client_runtime = ClientAnalysisRuntime.from_project(PROJECT_ROOT)
        analysis_jobs = AnalysisJobRuntime.from_client_runtime(
            client_runtime,
            checkpoint_root=Path(cls.temporary_runtime.name) / "checkpoints",
            repository=repository,
            observability=observability,
        )
        runtime = DashboardRuntime.from_project(
            PROJECT_ROOT,
            command_runner=_FakeRunner(),
            assistant=LocalProjectAssistant(),
            client_runtime=client_runtime,
            market_assistant=LocalMarketAssistant(),
            analysis_jobs=analysis_jobs,
            analysis_repository=repository,
            research_workspace=ResearchWorkspaceRuntime(
                repository,
                ReportViewRuntime(repository),
                InMemoryResearchWorkspaceStore(),
            ),
            observability=observability,
        )
        cls.runtime = runtime
        cls.server = create_server(port=0, runtime=runtime)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.runtime.analysis_jobs.close()
        cls.temporary_runtime.cleanup()

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

    def test_workspace_http_supports_snapshot_and_watchlist_toggle(self):
        with urlopen(f"{self.base_url}/api/client/workspace", timeout=2) as response:
            initial = json.loads(response.read().decode("utf-8"))
        toggle = Request(
            f"{self.base_url}/api/client/workspace/watchlist",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"symbol": "sz000001"}).encode("utf-8"),
        )
        with urlopen(toggle, timeout=2) as response:
            added = json.loads(response.read().decode("utf-8"))

        self.assertIn("reports", initial)
        self.assertTrue(initial["frozen_data_only"])
        self.assertTrue(added["added"])
        self.assertEqual(added["workspace"]["watchlist"][0]["symbol"], "sz000001")

        with urlopen(toggle, timeout=2) as response:
            removed = json.loads(response.read().decode("utf-8"))
        self.assertFalse(removed["added"])

    def test_workspace_http_supports_favorite_and_both_export_depths(self):
        reports = []
        for _ in range(2):
            job = self.server.runtime.submit_client_analysis(
                {"symbol": "sz000001", "mode": "offline"}
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = self.server.runtime.get_client_analysis_job(job["job_id"])
                if status["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.02)
            self.assertEqual(status["status"], "succeeded", status)
            reports.append(
                self.server.runtime.get_client_analysis_result(job["job_id"])
            )

        favorite_request = Request(
            f"{self.base_url}/api/client/workspace/favorites",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"report_id": reports[0]["report_id"]}).encode("utf-8"),
        )
        with urlopen(favorite_request, timeout=2) as response:
            favorite = json.loads(response.read().decode("utf-8"))
        self.assertTrue(favorite["added"])

        export_base = (
            f"{self.base_url}/api/client/reports/{reports[0]['report_id']}/export"
        )
        with urlopen(f"{export_base}?view=basic", timeout=2) as response:
            basic = response.read().decode("utf-8")
            disposition = response.headers["Content-Disposition"]
        with urlopen(f"{export_base}?view=professional", timeout=2) as response:
            professional = response.read().decode("utf-8")
        comparison_url = (
            f"{self.base_url}/api/client/workspace/export?"
            f"left_report_id={reports[0]['report_id']}&"
            f"right_report_id={reports[1]['report_id']}&view=professional"
        )
        with urlopen(comparison_url, timeout=2) as response:
            comparison = response.read().decode("utf-8")

        self.assertIn("attachment", disposition)
        self.assertIn("研究摘要", basic)
        self.assertNotIn("证据来源", basic)
        self.assertIn("证据来源", professional)
        self.assertIn("风险边界", professional)
        self.assertIn("同一股票前后变化", comparison)
        self.assertIn("计划仓位上限", comparison)
        self.assertIn("预计单次亏损", comparison)

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

    def test_history_delete_requires_confirmation_and_removes_report(self):
        analyze = Request(
            f"{self.base_url}/api/client/jobs", method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"symbol": "sz000001", "mode": "offline"}).encode("utf-8"),
        )
        with urlopen(analyze, timeout=5) as response:
            job = json.loads(response.read().decode("utf-8"))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with urlopen(f"{self.base_url}/api/client/jobs/{job['job_id']}", timeout=5) as response:
                status = json.loads(response.read().decode("utf-8"))
            if status["status"] == "succeeded":
                break
            time.sleep(0.05)
        with urlopen(f"{self.base_url}/api/client/jobs/{job['job_id']}/result", timeout=5) as response:
            report = json.loads(response.read().decode("utf-8"))

        unconfirmed = Request(
            f"{self.base_url}/api/client/reports/{report['report_id']}", method="DELETE"
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(unconfirmed, timeout=2)
        self.assertEqual(raised.exception.code, 400)

        confirmed = Request(
            f"{self.base_url}/api/client/reports/{report['report_id']}", method="DELETE",
            headers={"X-Confirm-Delete": "delete-one"},
        )
        with urlopen(confirmed, timeout=2) as response:
            deleted = json.loads(response.read().decode("utf-8"))
        with urlopen(f"{self.base_url}/api/client/history", timeout=2) as response:
            history = json.loads(response.read().decode("utf-8"))

        self.assertEqual(deleted["status"], "deleted")
        self.assertNotIn(report["report_id"], {item["report_id"] for item in history["reports"]})

    def test_report_view_http_supports_basic_and_professional_without_new_analysis(self):
        analyze = Request(
            f"{self.base_url}/api/client/jobs", method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"symbol": "sz000001", "mode": "offline"}).encode("utf-8"),
        )
        with urlopen(analyze, timeout=5) as response:
            job = json.loads(response.read().decode("utf-8"))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with urlopen(f"{self.base_url}/api/client/jobs/{job['job_id']}", timeout=5) as response:
                status = json.loads(response.read().decode("utf-8"))
            if status["status"] == "succeeded":
                break
            time.sleep(0.05)
        with urlopen(f"{self.base_url}/api/client/jobs/{job['job_id']}/result", timeout=5) as response:
            report = json.loads(response.read().decode("utf-8"))

        with urlopen(f"{self.base_url}/api/client/reports/{report['report_id']}/view?view=basic", timeout=2) as response:
            basic = json.loads(response.read().decode("utf-8"))
        with urlopen(f"{self.base_url}/api/client/reports/{report['report_id']}/view?view=professional", timeout=2) as response:
            professional = json.loads(response.read().decode("utf-8"))

        self.assertEqual(basic["projection_fingerprint"], professional["projection_fingerprint"])
        self.assertNotIn("professional", basic)
        self.assertEqual(len(professional["professional"]["agent_details"]), 4)

        invalid = Request(
            f"{self.base_url}/api/client/reports/{report['report_id']}/view?view=admin"
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(invalid, timeout=2)
        self.assertEqual(raised.exception.code, 400)

    def test_clear_history_requires_distinct_confirmation(self):
        unconfirmed = Request(f"{self.base_url}/api/client/history", method="DELETE")
        with self.assertRaises(HTTPError) as raised:
            urlopen(unconfirmed, timeout=2)
        self.assertEqual(raised.exception.code, 400)

        confirmed = Request(
            f"{self.base_url}/api/client/history", method="DELETE",
            headers={"X-Confirm-Delete": "clear-all"},
        )
        with urlopen(confirmed, timeout=2) as response:
            result = json.loads(response.read().decode("utf-8"))

        self.assertEqual(result["status"], "cleared")

    def test_second_dashboard_on_same_port_is_rejected_clearly(self):
        with self.assertRaisesRegex(DashboardError, "已有后台在运行"):
            create_server(port=self.server.server_port, runtime=self.server.runtime)


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
        self.assertIn('id="clear-history-button"', client_html)
        self.assertIn('id="history-confirm"', client_html)
        self.assertIn('id="research-workspace"', client_html)
        self.assertIn('id="watchlist-toggle"', client_html)
        self.assertIn('id="compare-left"', client_html)
        self.assertIn('id="compare-right"', client_html)
        self.assertIn('id="comparison-result"', client_html)
        self.assertIn('id="favorite-report-button"', client_html)
        self.assertIn('id="export-report-button"', client_html)
        self.assertIn('id="print-report-button"', client_html)
        self.assertIn('data-history-filter="favorites"', client_html)
        self.assertIn("X-Confirm-Delete", client_javascript)
        self.assertIn("当前运行的是旧版后台", client_javascript)
        self.assertNotIn("服务返回了无法识别的内容", client_javascript)
        self.assertNotIn("if (!existing) return runClientAnalysis()", client_javascript)
        self.assertIn('if (!existing) {', client_javascript)
        self.assertIn('id="cancel-analysis-button"', client_html)
        self.assertIn('id="retry-job-button"', client_html)
        self.assertIn('id="snapshot-health"', client_html)
        self.assertIn('id="snapshot-datasets"', client_html)
        self.assertIn('data-report-view="basic"', client_html)
        self.assertIn('data-report-view="professional"', client_html)
        self.assertLess(
            client_html.index('<section class="view-depth"'),
            client_html.index('<section class="analysis" id="analysis" hidden>'),
            "普通版/专业版入口必须在报告结果出现前可见",
        )
        self.assertIn('id="professional-nodes"', client_html)
        self.assertIn('id="basic-guide"', client_html)
        self.assertIn('id="basic-risk-explanation"', client_html)
        self.assertIn('普通版 · 结论摘要', client_html)
        self.assertIn('核心结论与风险提示', client_html)
        self.assertNotIn('普通版 · 先说人话', client_html)
        self.assertNotIn('不用懂指标，先看这四句话', client_html)
        self.assertIn('class="chart-card" data-professional-only hidden', client_html)
        self.assertIn('class="ai-card" id="ai-card" data-professional-only hidden', client_html)
        self.assertIn('id="agent-drilldown"', client_html)
        self.assertIn('data-chart-period="weekly"', client_html)
        self.assertIn('data-chart-indicator="sma20"', client_html)
        self.assertIn("switchReportView", client_javascript)
        self.assertIn("projection_fingerprint", (PROJECT_ROOT / "src" / "agent_platform" / "report_views.py").read_text(encoding="utf-8"))
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
        self.assertIn("renderSnapshotHealth", client_javascript)
        self.assertIn("loadResearchWorkspace", client_javascript)
        self.assertIn("runReportComparison", client_javascript)
        self.assertIn("renderReportComparison", client_javascript)
        self.assertIn("toggleReportFavorite", client_javascript)
        self.assertIn("downloadExport", client_javascript)
        self.assertIn("printResearch", client_javascript)
        self.assertIn(".snapshot-dataset", client_css)
        self.assertIn(".comparison-rail", client_css)
        self.assertIn("@media print", client_css)
        self.assertIn("只重试失败步骤", client_html)
        self.assertIn(".job-stage", client_css)


if __name__ == "__main__":
    unittest.main()
