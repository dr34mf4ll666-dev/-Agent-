"""D2 Harness 工程化总验收的稳定 Python interface。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

from .core.contracts import (
    AgentRequest,
    AgentResponse,
    GuardrailViolation,
    HarnessExecutionError,
)
from .core.harness import AgentHarness
from .core.evaluation import (
    EvaluationCandidate,
    EvaluationDataset,
    HarnessComparisonRunner,
)
from .core.industrial_harness import (
    IndustrialHarness,
    IndustrialHarnessConfig,
    IndustrialHarnessExecutionError,
)


@dataclass(frozen=True)
class D2EngineeringReport:
    configuration: Mapping[str, Any]
    evaluator: Mapping[str, Any]
    comparison: Mapping[str, Any]
    circuit_breaker: Mapping[str, Any]
    tool_permissions: Mapping[str, Any]
    acceptance: Mapping[str, bool]

    @property
    def passed(self) -> bool:
        return all(self.acceptance.values())

    def to_mapping(self) -> dict[str, Any]:
        return {
            "configuration": dict(self.configuration),
            "evaluator": dict(self.evaluator),
            "comparison": dict(self.comparison),
            "circuit_breaker": dict(self.circuit_breaker),
            "tool_permissions": dict(self.tool_permissions),
            "acceptance": dict(self.acceptance),
            "passed": self.passed,
        }


class _SequenceClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class D2EngineeringRuntime:
    """加载固定配置和数据集，一次返回 D2 全部可验收结果。"""

    def __init__(
        self,
        *,
        config: IndustrialHarnessConfig,
        dataset: EvaluationDataset,
    ) -> None:
        self._config = config
        self._dataset = dataset

    @classmethod
    def from_files(
        cls,
        *,
        config_path: str | Path | None = None,
        dataset_path: str | Path | None = None,
    ) -> "D2EngineeringRuntime":
        config_value = _load_json(
            config_path,
            resource_name="d2_harness_config.json",
        )
        dataset_value = _load_json(
            dataset_path,
            resource_name="d2_evaluation_dataset.json",
        )
        return cls(
            config=IndustrialHarnessConfig.from_mapping(config_value),
            dataset=EvaluationDataset.from_mapping(dataset_value),
        )

    def run(self) -> D2EngineeringReport:
        without_harness, with_harness = _run_fixed_scripted_experiment(
            self._dataset
        )
        comparison = HarnessComparisonRunner().compare(
            self._dataset,
            without_harness=without_harness,
            with_harness=with_harness,
        )
        protected = comparison.with_harness
        circuit = self._verify_circuit_breaker()
        permissions = self._verify_tool_permissions()
        protected_summary = protected.summary
        comparison_mapping = comparison.to_mapping()
        acceptance = {
            "固定数据集由独立 Evaluator 确定性评分": (
                protected_summary["case_count"] == len(self._dataset.cases)
                and protected_summary["average_score"] == 100.0
            ),
            "连续三次失败后自动熔断并暂停": (
                circuit["state"] == "open"
                and circuit["paused"] is True
                and circuit["operation_calls"] == 3
            ),
            "熔断时产生可审计告警": (
                circuit["alert_code"] == "agent_circuit_opened"
                and circuit["blocked_code"] == "circuit_open"
            ),
            "未知或越权工具在执行前被拒绝": (
                permissions["denied_before_execution"] is True
                and permissions["operation_calls"] == 0
            ),
            "每个当前 Agent 都有显式工具白名单": (
                permissions["policy_count"] == 9
                and permissions["unknown_agent_denied"] is True
            ),
            "同任务同数据的 Harness 对比指标齐全": all(
                key in comparison_mapping["improvement"]
                for key in (
                    "hallucination_rate_change_points",
                    "invalid_api_calls_change",
                    "success_rate_change_points",
                    "average_latency_change_ms",
                    "token_cost_change",
                    "recovery_success_rate_percent",
                )
            ),
        }
        return D2EngineeringReport(
            configuration={
                "config_version": 1,
                "dataset": self._dataset.name,
                "dataset_version": self._dataset.version,
                "failure_threshold": self._config.failure_threshold,
                "reset_timeout_seconds": self._config.reset_timeout_seconds,
            },
            evaluator=protected.to_mapping(),
            comparison=comparison_mapping,
            circuit_breaker=circuit,
            tool_permissions=permissions,
            acceptance=acceptance,
        )

    def _verify_circuit_breaker(self) -> dict[str, Any]:
        clock = _SequenceClock()
        harness = IndustrialHarness(
            self._config,
            clock=clock,
            wall_clock=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        operation_calls = 0
        alert_code = ""

        def fail() -> None:
            nonlocal operation_calls
            operation_calls += 1
            raise RuntimeError("deterministic evaluator failure")

        for _ in range(self._config.failure_threshold):
            try:
                harness.run(agent="echo", operation=fail)
            except IndustrialHarnessExecutionError as error:
                if error.alerts:
                    alert_code = error.alerts[0].code

        blocked_code = ""
        try:
            harness.run(agent="echo", operation=fail)
        except IndustrialHarnessExecutionError as error:
            blocked_code = error.code
        return {
            **harness.circuit_snapshot("echo"),
            "operation_calls": operation_calls,
            "alert_code": alert_code,
            "blocked_code": blocked_code,
        }

    def _verify_tool_permissions(self) -> dict[str, Any]:
        harness = IndustrialHarness(self._config)
        operation_calls = 0

        def operation() -> str:
            nonlocal operation_calls
            operation_calls += 1
            return "must not run"

        denied_before_execution = False
        try:
            harness.run(
                agent="gateway_research_planner",
                operation=operation,
                requested_tools=("web_search",),
            )
        except IndustrialHarnessExecutionError as error:
            denied_before_execution = (
                error.code == "tool_permission_denied"
                and error.operation_executed is False
            )

        unknown_agent_denied = False
        try:
            harness.tool_policies.allowed_tools("unregistered_agent")
        except PermissionError:
            unknown_agent_denied = True
        return {
            "policy_count": len(harness.tool_policies.agents),
            "policies": {
                agent: list(harness.tool_policies.allowed_tools(agent))
                for agent in harness.tool_policies.agents
            },
            "denied_before_execution": denied_before_execution,
            "unknown_agent_denied": unknown_agent_denied,
            "operation_calls": operation_calls,
        }


def _load_json(
    path: str | Path | None,
    *,
    resource_name: str,
) -> Mapping[str, Any]:
    if path is None:
        resource = files("agent_platform.resources").joinpath(resource_name)
        text = resource.read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    value = json.loads(text)
    if not isinstance(value, Mapping):
        raise ValueError(f"{resource_name} must contain a JSON object")
    return value


class _ScriptedCandidateAgent:
    name = "d2_scripted_candidate"

    def __init__(self, candidate: EvaluationCandidate) -> None:
        self._candidate = candidate

    def run(self, request: AgentRequest) -> AgentResponse:
        del request
        return AgentResponse(
            content=self._candidate.answer or "empty candidate",
            metadata={"candidate": self._candidate},
        )


class _FixedCaseGuardrail:
    name = "d2_fixed_case_rules"

    def __init__(self, case) -> None:
        self._case = case

    def check_input(self, request: AgentRequest) -> None:
        requested = tuple(request.context.get("requested_tools", ()))
        unauthorized = tuple(
            tool for tool in requested if tool not in self._case.allowed_tools
        )
        if unauthorized:
            raise GuardrailViolation(
                f"unauthorized tools rejected before execution: {list(unauthorized)!r}"
            )

    def check_output(self, response: AgentResponse) -> None:
        candidate = response.metadata.get("candidate")
        if not isinstance(candidate, EvaluationCandidate):
            raise GuardrailViolation("candidate metadata is missing")
        if dict(candidate.claims) != dict(self._case.expected_facts):
            raise GuardrailViolation("claims do not match deterministic evidence")
        if any(
            phrase in candidate.answer for phrase in self._case.forbidden_phrases
        ):
            raise GuardrailViolation("forbidden phrase present")


def _run_fixed_scripted_experiment(dataset: EvaluationDataset):
    initial_candidates, recovery_candidates = _scripted_candidates()
    without_harness: list[EvaluationCandidate] = []
    with_harness: list[EvaluationCandidate] = []
    initial_by_id = {candidate.case_id: candidate for candidate in initial_candidates}
    recovery_by_id = {
        candidate.case_id: candidate for candidate in recovery_candidates
    }

    for case in dataset.cases:
        initial = initial_by_id[case.case_id]
        without_harness.append(initial)
        request = AgentRequest(
            task=case.task,
            context={"requested_tools": list(initial.executed_tools)},
        )
        try:
            result = AgentHarness(
                _ScriptedCandidateAgent(initial),
                (_FixedCaseGuardrail(case),),
            ).run(request)
            with_harness.append(result.response.metadata["candidate"])
        except HarnessExecutionError:
            recovery = recovery_by_id[case.case_id]
            recovery_result = AgentHarness(
                _ScriptedCandidateAgent(recovery),
                (_FixedCaseGuardrail(case),),
            ).run(
                AgentRequest(
                    task=case.task,
                    context={"requested_tools": list(recovery.executed_tools)},
                )
            )
            accepted = recovery_result.response.metadata["candidate"]
            with_harness.append(
                replace(
                    accepted,
                    latency_ms=initial.latency_ms + accepted.latency_ms,
                    total_tokens=initial.total_tokens + accepted.total_tokens,
                    recovery_attempted=True,
                )
            )
    return tuple(without_harness), tuple(with_harness)


def _scripted_candidates():
    initial = (
        EvaluationCandidate(
            "source-grounding",
            "来源已确认",
            {"source": "fixture_document"},
            executed_tools=("local_document_search",),
            latency_ms=40,
            total_tokens=20,
        ),
        EvaluationCandidate(
            "schema-completion",
            "分析完成",
            {"status": "done"},
            latency_ms=40,
            total_tokens=20,
        ),
        EvaluationCandidate(
            "tool-permission",
            "已联网搜索",
            {"tool": "web_search"},
            executed_tools=("web_search",),
            latency_ms=40,
            total_tokens=20,
        ),
        EvaluationCandidate(
            "financial-language-safety",
            "该方案绝对稳赚",
            {"real_trading_allowed": True},
            latency_ms=40,
            total_tokens=20,
        ),
    )
    recovery = (
        initial[0],
        EvaluationCandidate(
            "schema-completion",
            "结构化状态已复核",
            {"status": "completed", "simulation_only": True},
            latency_ms=60,
            total_tokens=15,
        ),
        EvaluationCandidate(
            "tool-permission",
            "越权工具被拒绝，改用本地资料",
            {"tool": "local_document_search"},
            executed_tools=("local_document_search",),
            latency_ms=70,
            total_tokens=14,
        ),
        EvaluationCandidate(
            "financial-language-safety",
            "仅提供模拟研究结论，不构成投资建议",
            {"real_trading_allowed": False},
            latency_ms=55,
            total_tokens=11,
        ),
    )
    return initial, recovery
