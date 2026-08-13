"""Local Web control console for the complete A-D Agent platform."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import webbrowser
from collections import OrderedDict
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Timer
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlparse

from .analysis_jobs import AnalysisJobError, AnalysisJobRuntime
from .client_app import (
    ClientAnalysisError,
    ClientAnalysisRequest,
    ClientAnalysisRuntime,
    MarketAssistant,
    SECURITIES,
    build_default_market_assistant,
)
from .core import (
    DeepSeekChatAdapter,
    ModelGateway,
    ModelGatewayConfigurationError,
    ModelGatewayExecutionError,
    ModelRequest,
    ModelRetryPolicy,
)
from .finance import (
    DynamicDebateRuntime,
    StructuredDebateQuery,
    build_default_dynamic_debate_runtime,
)
from uuid import uuid4


class DashboardError(ValueError):
    """A dashboard request is invalid or cannot be executed safely."""


@dataclass(frozen=True)
class ActionSpec:
    id: str
    stage: str
    title: str
    description: str
    script: str
    arguments: tuple[str, ...] = ()
    live_arguments: tuple[str, ...] | None = None
    summary_prefixes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @property
    def supports_live(self) -> bool:
        return self.live_arguments is not None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stage": self.stage,
            "title": self.title,
            "description": self.description,
            "supports_live": self.supports_live,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class CommandExecution:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int


class DashboardCommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandExecution:
        """Run one allowlisted project action."""


class SubprocessDashboardCommandRunner:
    """Production process adapter with bounded runtime and UTF-8 output."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandExecution:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                list(command),
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout if isinstance(error.stdout, str) else ""
            stderr = error.stderr if isinstance(error.stderr, str) else ""
            return CommandExecution(
                returncode=124,
                stdout=stdout,
                stderr=(stderr + "\n运行超时，控制台已终止本次任务。").strip(),
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        return CommandExecution(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )


STAGES = (
    {
        "id": "A",
        "eyebrow": "平台底座",
        "title": "A · 通用 Agent 能力",
        "description": "验证 Harness、Graph、Loop、记忆、模型调用与跨领域复用。",
        "color": "cobalt",
    },
    {
        "id": "B",
        "eyebrow": "金融研究",
        "title": "B · 数据与四类分析 Agent",
        "description": "接入金融数据，由技术、基本面、行业和宏观 Agent 分工分析。",
        "color": "mint",
    },
    {
        "id": "C",
        "eyebrow": "联合决策",
        "title": "C · 多 Agent 编排与风控",
        "description": "汇总四类研究，经过 Trader、条件路由和 Risk Manager 形成模拟决策。",
        "color": "coral",
    },
    {
        "id": "D",
        "eyebrow": "工程验收",
        "title": "D · 回测、评估与模拟运行",
        "description": "用回测、Evaluator、Harness 对照实验和持续模拟验证系统质量。",
        "color": "violet",
    },
)


ACTIONS = (
    ActionSpec(
        "a1_guardrails", "A", "A1 · Harness 安全护栏",
        "查看输入输出校验、来源约束、关键词拦截、限流和交叉验证。",
        "demo_guardrails.py",
        summary_prefixes=("安全输出:", "关键词拦截:", "限流拦截:"),
        tags=("Guardrail", "可观测"),
    ),
    ActionSpec(
        "a2_graph", "A", "A2 · Graph 工作流",
        "验证节点编排、状态传递、条件路由、失败恢复与 Checkpoint。",
        "demo_graph_engineering.py",
        summary_prefixes=("工作流:", "execution_order:", "checkpoint:"),
        tags=("Graph", "Checkpoint"),
    ),
    ActionSpec(
        "a3_loop", "A", "A3 · Loop 与记忆",
        "展示 Agent 如何计划、行动、观察、反思，并使用工作记忆持续推进。",
        "demo_loop_engineering.py",
        summary_prefixes=("scenario:", "status:", "result:"),
        tags=("Loop", "Memory"),
    ),
    ActionSpec(
        "a4_model", "A", "A4 · 统一模型网关",
        "验证统一模型接口、结构化输出、重试、耗时与 Token 统计。",
        "demo_model_gateway.py",
        live_arguments=("--live", "--provider", "deepseek"),
        summary_prefixes=("provider:", "model:", "status:", "tokens:", "latency_ms:"),
        tags=("DeepSeek", "Model Gateway"),
    ),
    ActionSpec(
        "a5_research", "A", "A5 · 非金融资料研究",
        "证明平台不只会分析股票，也能检索资料、整理证据并生成有来源的报告。",
        "demo_non_financial_research.py",
        live_arguments=("--live",),
        summary_prefixes=("mode:", "topic:", "evidence_count:", "summary:", "model_calls:"),
        tags=("检索", "跨领域"),
    ),
    ActionSpec(
        "b1_data", "B", "B1 · 金融 Data Hub",
        "统一读取行情、财务、行业和宏观数据，并保留来源与数据时间。",
        "demo_financial_data_hub.py",
        live_arguments=(
            "--live", "--dataset", "market.daily", "--symbol", "sz000001",
            "--start-date", "20260701", "--end-date", "20260807",
        ),
        summary_prefixes=("mode:", "- market.", "- company.", "- industry.", "- macro."),
        tags=("真实数据", "Data Hub"),
    ),
    ActionSpec(
        "b2_technical", "B", "B2 · 技术分析 Agent",
        "计算均线、MACD、RSI、KDJ、布林带和支撑阻力等确定性指标。",
        "demo_technical_analysis.py",
        live_arguments=("--live",),
        summary_prefixes=("模式（mode）:", "标的（symbol）:", "收盘价", "均线", "趋势", "信号"),
        tags=("技术指标", "确定性计算"),
    ),
    ActionSpec(
        "b2_fundamental", "B", "B2 · 基本面 Agent",
        "分析收入、利润、资产负债与现金流，不让模型自行编造财务数字。",
        "demo_fundamental_analysis.py",
        live_arguments=("--live",),
        summary_prefixes=("mode:", "symbol:", "score:", "conclusion:"),
        tags=("财务数据", "基本面"),
    ),
    ActionSpec(
        "b2_industry", "B", "B2 · 行业 Agent",
        "分析行业景气、资金表现和政策信息，并保留证据来源。",
        "demo_industry_analysis.py",
        live_arguments=("--live",),
        summary_prefixes=("mode:", "sector:", "score:", "conclusion:"),
        tags=("行业", "政策证据"),
    ),
    ActionSpec(
        "b2_macro", "B", "B2 · 宏观 Agent",
        "结合大盘趋势、资金和宏观数据判断当前市场环境。",
        "demo_macro_analysis.py",
        live_arguments=("--live",),
        summary_prefixes=("mode:", "index_symbol:", "score:", "regime:"),
        tags=("宏观", "市场环境"),
    ),
    ActionSpec(
        "c1_research", "C", "C1 · 四 Agent 联合研究",
        "并行运行四类 Specialist，通过辩论与汇总形成统一研究结论。",
        "demo_combined_analysis.py",
        live_arguments=("--live",),
        summary_prefixes=("模式（mode）:", "C1 综合结论:", "置信度", "研究价格区间"),
        tags=("多 Agent", "Debate"),
    ),
    ActionSpec(
        "c1_debate_eval", "C", "C1+ · 动态辩论量化评测",
        "固定四 Agent 研究底稿，重复比较模板辩论与受约束动态辩论的证据、平衡、稳定性和成本。",
        "demo_dynamic_debate_evaluation.py",
        live_arguments=("--live", "--no-key-prompt"),
        summary_prefixes=(
            "模式:", "评测集:", "- 候选证据有效率:", "- 最终证据有效率:",
            "- 观点多样性:", "- 正反平衡率:", "- 重试率:", "- 降级率:",
            "- 平均耗时:", "- Token:", "- 结果稳定性:", "结论:",
        ),
        tags=("DeepSeek", "固定评测"),
    ),
    ActionSpec(
        "c2_risk", "C", "C2 · Trader 与 Risk Manager",
        "把研究结论转为候选动作，再计算仓位、止损和预计单笔亏损。",
        "demo_c2_trading.py", ("--confirm",), ("--confirm", "--live"),
        summary_prefixes=("Trader 候选:", "最终模拟决策:", "approved_position", "estimated_single_trade_loss"),
        tags=("仓位", "风控"),
    ),
    ActionSpec(
        "c3_graph", "C", "C3 · 完整金融 Graph",
        "从四 Agent 研究到交易候选、条件路由、风控和标准化报告一次跑通。",
        "demo_financial_graph.py", ("--confirm",), ("--confirm", "--live"),
        summary_prefixes=("模式（mode）:", "顶层 Graph:", "C1 综合结论:", "最终模拟决策:", "C3 最终标准化金融分析报告"),
        tags=("完整链路", "标准报告"),
    ),
    ActionSpec(
        "d1_backtest", "D", "D1 · 多股票回测",
        "用历史数据检验 C 阶段决策规则，展示收益、回撤、夏普和基准对比。",
        "demo_backtest_experiment.py",
        summary_prefixes=("组合收益率", "最大回撤", "年化夏普", "总体结果:"),
        tags=("回测", "无未来数据"),
    ),
    ActionSpec(
        "d2_engineering", "D", "D2 · Harness 工程验收",
        "查看独立 Evaluator、熔断告警、最小权限和可恢复执行。",
        "demo_d2_engineering.py",
        summary_prefixes=("用例=", "阈值=", "结论:"),
        tags=("Evaluator", "熔断"),
    ),
    ActionSpec(
        "d3_comparison", "D", "D3 · Harness 价值对照",
        "在同任务和同数据下比较有无 Harness 的幻觉率、调用、成功率、耗时与成本。",
        "demo_harness_comparison.py",
        summary_prefixes=("幻觉率", "无效工具/API 调用", "端到端成功率", "平均耗时", "Token 总成本", "结论:"),
        tags=("对照实验", "量化指标"),
    ),
    ActionSpec(
        "d4_paper", "D", "D4 · 持续模拟交易",
        "将 C3 决策送入本地模拟撮合并写入账本；永远不会创建真实订单。",
        "demo_paper_trading.py",
        ("--confirm",),
        (
            "--confirm", "--live", "--session-id", "dashboard-live",
            "--ledger", ".runtime/paper_trading/dashboard-live.json",
        ),
        summary_prefixes=("本次状态:", "数据模式:", "账户现金:", "持仓:", "real_trading_allowed="),
        tags=("Paper Trading", "模拟撮合"),
    ),
    ActionSpec(
        "final_delivery", "D", "最终 · 一键项目验收",
        "顺序复现主要流程、检查文档和安全边界，给出完整交付结论。",
        "demo_final_delivery.py",
        summary_prefixes=("D4 调整后验收通过", "边界:"),
        tags=("一键验收", "交付"),
    ),
)


ACTION_BY_ID = {action.id: action for action in ACTIONS}


class ProjectAssistant(Protocol):
    provider: str
    model: str
    live: bool

    def answer(self, message: str, context: Mapping[str, Any] | None) -> dict[str, Any]:
        """Explain a result and suggest, but never execute, one action."""


class LocalProjectAssistant:
    """Deterministic fallback that keeps the console useful without an API key."""

    provider = "local"
    model = "rule-based-guide"
    live = False

    def answer(self, message: str, context: Mapping[str, Any] | None) -> dict[str, Any]:
        text = message.strip()
        suggested = "c3_graph"
        keyword_routes = (
            (("验收", "全部", "交付"), "final_delivery"),
            (("回测", "收益", "回撤", "夏普"), "d1_backtest"),
            (("真实数据", "数据源", "行情"), "b1_data"),
            (("四个", "四类", "联合", "研究"), "c1_research"),
            (("仓位", "止损", "风险"), "c2_risk"),
            (("harness", "幻觉", "对照"), "d3_comparison"),
            (("模拟交易", "账本", "撮合"), "d4_paper"),
        )
        for words, action_id in keyword_routes:
            if any(word in text.lower() for word in words):
                suggested = action_id
                break
        spec = ACTION_BY_ID[suggested]
        context_note = ""
        if context and context.get("summary"):
            context_note = "我已参考你当前选中的运行摘要。"
        return {
            "answer": (
                f"{context_note} 这个控制台把 A 到 D 串成一条链：A 提供平台能力，"
                "B 负责取数和专业分析，C 形成受风控约束的模拟决策，D 用回测与工程实验验收。"
                "当前是本地规则助手；设置 DEEPSEEK_API_KEY 后，解释会改由 DeepSeek 完成。"
            ).strip(),
            "suggested_action_id": suggested,
            "reason": f"你的问题与“{spec.title}”最接近。按钮只会填入建议，不会自动执行。",
            "provider": self.provider,
            "model": self.model,
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "latency_ms": 0,
        }


class DeepSeekProjectAssistant:
    provider = "deepseek"
    live = True

    def __init__(self, gateway: ModelGateway, *, model: str) -> None:
        self._gateway = gateway
        self.model = model

    @classmethod
    def from_env(cls) -> "DeepSeekProjectAssistant":
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        adapter = DeepSeekChatAdapter.from_env(model=model)
        gateway = ModelGateway(
            adapter,
            retry_policy=ModelRetryPolicy(
                max_attempts=2,
                timeout_seconds=30,
                initial_backoff_seconds=0.25,
            ),
        )
        return cls(gateway, model=model)

    def answer(self, message: str, context: Mapping[str, Any] | None) -> dict[str, Any]:
        action_ids = ["none", *ACTION_BY_ID]
        schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "minLength": 1},
                "suggested_action_id": {"type": "string", "enum": action_ids},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["answer", "suggested_action_id", "reason"],
            "additionalProperties": False,
        }
        safe_context = _bounded_context(context)
        prompt = json.dumps(
            {"question": message, "current_result": safe_context},
            ensure_ascii=False,
        )
        result = self._gateway.generate(
            ModelRequest(
                prompt=prompt,
                system_prompt=(
                    "你是该项目控制台中的中文讲解助手。只能依据给定运行结果解释，不能编造数据，"
                    "不能提供保证收益的投资建议，不能声称已经下单。确定性指标、仓位和风控结果"
                    "以程序输出为准。你可以从白名单 action id 中建议下一项，但不能执行它。"
                ),
                response_schema=schema,
                schema_name="dashboard_assistant",
                max_output_tokens=500,
            )
        )
        output = dict(result.response.structured_output)
        output.update(
            {
                "provider": result.response.provider,
                "model": result.response.model,
                "usage": {
                    "input_tokens": result.response.usage.input_tokens,
                    "output_tokens": result.response.usage.output_tokens,
                    "total_tokens": result.response.usage.total_tokens,
                },
                "latency_ms": result.response.latency_ms,
            }
        )
        return output


def _bounded_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        return {}
    return {
        "action_id": str(context.get("action_id", ""))[:80],
        "title": str(context.get("title", ""))[:160],
        "summary": str(context.get("summary", ""))[:4000],
    }


class DashboardRuntime:
    """One stable interface behind the Web console."""

    def __init__(
        self,
        *,
        project_root: Path,
        command_runner: DashboardCommandRunner | None = None,
        assistant: ProjectAssistant | None = None,
        client_runtime: ClientAnalysisRuntime | None = None,
        market_assistant: MarketAssistant | None = None,
        dynamic_debate_runtime: DynamicDebateRuntime | None = None,
        analysis_jobs: AnalysisJobRuntime | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.project_root = project_root.resolve()
        self.command_runner = command_runner or SubprocessDashboardCommandRunner()
        self.assistant = assistant or build_default_assistant()
        self.client_runtime = client_runtime or ClientAnalysisRuntime.from_project(
            self.project_root
        )
        self.market_assistant = market_assistant or build_default_market_assistant()
        self.dynamic_debate_runtime = (
            dynamic_debate_runtime or build_default_dynamic_debate_runtime()
        )
        self.analysis_jobs = analysis_jobs or AnalysisJobRuntime.from_client_runtime(
            self.client_runtime,
            storage_path=self.project_root / ".runtime" / "analysis_jobs" / "jobs.json",
            checkpoint_root=self.project_root / ".runtime" / "analysis_jobs" / "checkpoints",
            timeout_seconds=180.0,
        )
        self._debate_contexts: OrderedDict[str, Mapping[str, Any]] = OrderedDict()
        self._debate_context_lock = Lock()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_project(
        cls,
        project_root: Path | None = None,
        **kwargs: Any,
    ) -> "DashboardRuntime":
        root = project_root or Path(__file__).resolve().parents[2]
        return cls(project_root=root, **kwargs)

    def overview(self) -> dict[str, Any]:
        return {
            "project": {
                "name": "通用 Agent 平台 · 金融分析应用",
                "description": "从通用 Agent 底座，到真实金融数据、多 Agent 决策与工程验收的一体化控制台。",
                "version": "0.1.0",
            },
            "stages": list(STAGES),
            "actions": [action.to_mapping() for action in ACTIONS],
            "assistant": {
                "provider": self.assistant.provider,
                "model": self.assistant.model,
                "live": self.assistant.live,
                "configured": self.assistant.provider == "deepseek",
            },
            "safety": {
                "simulation_only": True,
                "real_trading_allowed": False,
                "order_created": False,
                "note": "真实数据只读；交易动作只进入本地模拟撮合。",
            },
        }

    def client_overview(self) -> dict[str, Any]:
        return {
            "product": {
                "name": "研判 · 多维证券研究助手",
                "description": "把行情、经营、行业和市场环境放在一张报告里。",
            },
            "securities": [
                {
                    "symbol": symbol,
                    "code": symbol[2:],
                    "name": value["name"],
                    "exchange": value["exchange"],
                    "modes": list(value["sectors"]),
                }
                for symbol, value in SECURITIES.items()
            ],
            "capabilities": [
                "K 线与技术指标",
                "经营质量与估值",
                "行业景气与政策",
                "市场环境与资金",
                "多观点综合研判",
                "风险区间与智能解读",
            ],
            "assistant": {
                "provider": self.market_assistant.provider,
                "model": self.market_assistant.model,
                "live": self.market_assistant.live,
            },
            "safety": {
                "research_only": True,
                "real_trading_allowed": False,
                "notice": "仅供研究与教学，不构成投资建议。",
            },
        }

    def analyze_client(self, value: Mapping[str, Any]) -> dict[str, Any]:
        try:
            request = ClientAnalysisRequest.from_mapping(value)
            result = self.client_runtime.analyze(request)
            return self._register_client_result(result)
        except ClientAnalysisError as error:
            raise DashboardError(str(error)) from error

    def submit_client_analysis(self, value: Mapping[str, Any]) -> dict[str, Any]:
        try:
            request = ClientAnalysisRequest.from_mapping(value)
            return self.analysis_jobs.submit(request)
        except (ClientAnalysisError, AnalysisJobError) as error:
            raise DashboardError(str(error)) from error

    def get_client_analysis_job(self, job_id: str) -> dict[str, Any]:
        try:
            return self.analysis_jobs.get(job_id)
        except AnalysisJobError as error:
            raise DashboardError(str(error)) from error

    def cancel_client_analysis_job(self, job_id: str) -> dict[str, Any]:
        try:
            return self.analysis_jobs.cancel(job_id)
        except AnalysisJobError as error:
            raise DashboardError(str(error)) from error

    def retry_client_analysis_job(self, job_id: str) -> dict[str, Any]:
        try:
            return self.analysis_jobs.retry(job_id)
        except AnalysisJobError as error:
            raise DashboardError(str(error)) from error

    def get_client_analysis_result(self, job_id: str) -> dict[str, Any]:
        try:
            return self._register_client_result(self.analysis_jobs.result(job_id))
        except AnalysisJobError as error:
            raise DashboardError(str(error)) from error

    def close(self) -> None:
        self.analysis_jobs.close(wait=False)

    def _register_client_result(self, result: Any) -> dict[str, Any]:
        response = result.to_mapping()
        if result.debate_context is not None:
            analysis_id = uuid4().hex
            with self._debate_context_lock:
                self._debate_contexts[analysis_id] = result.debate_context
                while len(self._debate_contexts) > 32:
                    self._debate_contexts.popitem(last=False)
            response["analysis_id"] = analysis_id
        return response

    def debate_client(self, analysis_id: str) -> dict[str, Any]:
        if not isinstance(analysis_id, str) or not analysis_id.strip():
            raise DashboardError("缺少可用于动态辩论的分析编号。")
        with self._debate_context_lock:
            context = self._debate_contexts.get(analysis_id.strip())
        if context is None:
            raise DashboardError("分析编号已失效，请重新完成一次股票分析。")
        return self.dynamic_debate_runtime.run(
            StructuredDebateQuery(context, rounds=2)
        ).to_mapping()

    def explain_client(self, analysis: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(analysis, Mapping):
            raise DashboardError("缺少可解释的分析结果。")
        safety = analysis.get("safety")
        if not isinstance(safety, Mapping) or (
            safety.get("simulation_only") is not True
            or safety.get("order_created") is not False
            or safety.get("real_trading_allowed") is not False
        ):
            raise DashboardError("分析结果没有通过研究安全边界校验。")
        return self.market_assistant.explain(analysis)

    def run_action(self, action_id: str, *, mode: str = "offline") -> dict[str, Any]:
        spec = ACTION_BY_ID.get(action_id)
        if spec is None:
            raise DashboardError("未知功能，控制台只允许运行登记过的项目功能。")
        if mode not in {"offline", "live"}:
            raise DashboardError("mode 必须是 offline 或 live。")
        if mode == "live" and not spec.supports_live:
            raise DashboardError("该功能只提供可复现的离线验收模式。")
        script = (self.project_root / "Scripts" / spec.script).resolve()
        scripts_root = (self.project_root / "Scripts").resolve()
        if script.parent != scripts_root or not script.is_file():
            raise DashboardError(f"项目功能入口不存在: {spec.script}")
        arguments = spec.arguments if mode == "offline" else spec.live_arguments
        assert arguments is not None
        execution = self.command_runner.run(
            (sys.executable, str(script), *arguments),
            cwd=self.project_root,
            timeout_seconds=self.timeout_seconds,
        )
        combined = execution.stdout.strip()
        if execution.stderr.strip():
            combined = f"{combined}\n\n[stderr]\n{execution.stderr.strip()}".strip()
        summary_lines = _extract_summary(execution.stdout, spec.summary_prefixes)
        if not summary_lines:
            summary_lines = [line for line in execution.stdout.splitlines() if line.strip()][:8]
        return {
            "action_id": spec.id,
            "stage": spec.stage,
            "title": spec.title,
            "mode": mode,
            "status": "succeeded" if execution.returncode == 0 else "failed",
            "returncode": execution.returncode,
            "duration_ms": execution.duration_ms,
            "summary": "\n".join(summary_lines),
            "raw_output": combined[-30000:],
            "safety": {
                "simulation_only": True,
                "real_trading_allowed": False,
                "order_created": False,
            },
        }

    def ask_assistant(
        self,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(message, str) or not message.strip():
            raise DashboardError("请先输入你想让助手解释的问题。")
        if len(message) > 1200:
            raise DashboardError("问题过长，请控制在 1200 字以内。")
        return self.assistant.answer(message.strip(), context)


def _extract_summary(stdout: str, prefixes: Sequence[str]) -> list[str]:
    selected: list[str] = []
    normalized = tuple(prefix.lower() for prefix in prefixes)
    for line in stdout.splitlines():
        clean = line.strip()
        if clean and any(clean.lower().startswith(prefix) for prefix in normalized):
            selected.append(clean)
    return selected[:16]


def build_default_assistant() -> ProjectAssistant:
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return LocalProjectAssistant()
    try:
        return DeepSeekProjectAssistant.from_env()
    except ModelGatewayConfigurationError:
        return LocalProjectAssistant()


class DashboardHTTPServer(ThreadingHTTPServer):
    runtime: DashboardRuntime

    def server_close(self) -> None:
        self.runtime.close()
        super().server_close()


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer
    static_root = Path(__file__).with_name("web")
    max_body_bytes = 32_768

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/overview":
            self._send_json(HTTPStatus.OK, self.server.runtime.overview())
            return
        if path == "/api/client/overview":
            self._send_json(HTTPStatus.OK, self.server.runtime.client_overview())
            return
        client_job = _match_client_job_path(path)
        if client_job is not None:
            job_id, operation = client_job
            try:
                result = (
                    self.server.runtime.get_client_analysis_result(job_id)
                    if operation == "result"
                    else self.server.runtime.get_client_analysis_job(job_id)
                )
                self._send_json(HTTPStatus.OK, result)
            except DashboardError as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        static_files = {
            "/": ("client.html", "text/html; charset=utf-8"),
            "/client.html": ("client.html", "text/html; charset=utf-8"),
            "/client.css": ("client.css", "text/css; charset=utf-8"),
            "/client.js": ("client.js", "text/javascript; charset=utf-8"),
            "/admin": ("index.html", "text/html; charset=utf-8"),
            "/admin/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        item = static_files.get(path)
        if item is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "页面不存在。"})
            return
        filename, content_type = item
        try:
            payload = (self.static_root / filename).read_bytes()
        except OSError:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "前端资源缺失。"})
            return
        self._send_bytes(HTTPStatus.OK, payload, content_type)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/run":
                result = self.server.runtime.run_action(
                    str(body.get("action_id", "")),
                    mode=str(body.get("mode", "offline")),
                )
                self._send_json(HTTPStatus.OK, result)
                return
            if path == "/api/assistant":
                result = self.server.runtime.ask_assistant(
                    body.get("message", ""),
                    context=body.get("context"),
                )
                self._send_json(HTTPStatus.OK, result)
                return
            if path == "/api/client/analyze":
                result = self.server.runtime.analyze_client(body)
                self._send_json(HTTPStatus.OK, result)
                return
            if path == "/api/client/jobs":
                result = self.server.runtime.submit_client_analysis(body)
                self._send_json(HTTPStatus.ACCEPTED, result)
                return
            client_job = _match_client_job_path(path)
            if client_job is not None and client_job[1] == "cancel":
                result = self.server.runtime.cancel_client_analysis_job(client_job[0])
                self._send_json(HTTPStatus.OK, result)
                return
            if client_job is not None and client_job[1] == "retry":
                result = self.server.runtime.retry_client_analysis_job(client_job[0])
                self._send_json(HTTPStatus.ACCEPTED, result)
                return
            if path == "/api/client/explain":
                result = self.server.runtime.explain_client(body.get("analysis", {}))
                self._send_json(HTTPStatus.OK, result)
                return
            if path == "/api/client/debate":
                result = self.server.runtime.debate_client(
                    str(body.get("analysis_id", ""))
                )
                self._send_json(HTTPStatus.OK, result)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})
        except DashboardError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except ModelGatewayExecutionError as error:
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                {"error": f"DeepSeek 调用失败: {error}", "code": error.code.value},
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "请求 JSON 无效。"})
        except Exception:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "服务内部错误，请查看终端日志。"})
            raise

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise DashboardError("Content-Length 无效。") from error
        if length <= 0 or length > self.max_body_bytes:
            raise DashboardError("请求体为空或超过 32 KB。")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise DashboardError("请求 JSON 必须是对象。")
        return value

    def _send_json(self, status: HTTPStatus, value: Mapping[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, payload, "application/json; charset=utf-8")

    def _send_bytes(self, status: HTTPStatus, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:",
        )
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[dashboard] {self.address_string()} - {format % args}")


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    runtime: DashboardRuntime | None = None,
) -> DashboardHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise DashboardError("控制台默认只允许绑定本机地址。")
    server = DashboardHTTPServer((host, port), DashboardRequestHandler)
    server.runtime = runtime or DashboardRuntime.from_project()
    return server


def serve_dashboard(*, port: int = 8765, open_browser: bool = True) -> None:
    server = create_server(port=port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print("=== 通用 Agent 平台 Web 控制台 ===")
    print(f"访问地址: {url}")
    print("安全边界: 仅监听本机；真实数据只读；交易仅本地模拟。")
    if open_browser:
        Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n控制台已停止。")
    finally:
        server.server_close()


def _match_client_job_path(path: str) -> tuple[str, str] | None:
    prefix = "/api/client/jobs/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix):].strip("/")
    parts = remainder.split("/") if remainder else []
    if len(parts) == 1 and parts[0]:
        return parts[0], "status"
    if len(parts) == 2 and parts[0] and parts[1] in {"result", "cancel", "retry"}:
        return parts[0], parts[1]
    return None
