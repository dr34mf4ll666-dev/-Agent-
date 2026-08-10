"""D2 工业化 Harness：运行级熔断、告警和 Agent 工具最小权限。"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class IndustrialHarnessConfigurationError(ValueError):
    """工业化 Harness 配置不完整或不安全。"""


class ToolPermissionError(PermissionError):
    """Agent 请求了未授权工具，或没有登记权限策略。"""


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IndustrialHarnessConfigurationError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class AgentToolPolicy:
    """一个 Agent 的最小工具允许列表。"""

    agent: str
    allowed_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        agent = _identifier(self.agent, "agent")
        tools = tuple(_identifier(tool, "tool") for tool in self.allowed_tools)
        if len(tools) != len(set(tools)):
            raise IndustrialHarnessConfigurationError(
                f"duplicate allowed tool for agent {agent}"
            )
        object.__setattr__(self, "agent", agent)
        object.__setattr__(self, "allowed_tools", tools)


class AgentToolPolicyRegistry:
    """集中验证 Agent 的最小工具权限，未知 Agent 默认拒绝。"""

    def __init__(self, policies: Iterable[AgentToolPolicy]) -> None:
        self._policies: dict[str, AgentToolPolicy] = {}
        for policy in policies:
            if not isinstance(policy, AgentToolPolicy):
                raise IndustrialHarnessConfigurationError(
                    "tool policies must be AgentToolPolicy values"
                )
            if policy.agent in self._policies:
                raise IndustrialHarnessConfigurationError(
                    f"duplicate agent tool policy: {policy.agent}"
                )
            self._policies[policy.agent] = policy
        if not self._policies:
            raise IndustrialHarnessConfigurationError(
                "at least one agent tool policy is required"
            )

    @property
    def agents(self) -> tuple[str, ...]:
        return tuple(self._policies)

    def allowed_tools(self, agent: str) -> tuple[str, ...]:
        agent = _identifier(agent, "agent")
        try:
            return self._policies[agent].allowed_tools
        except KeyError as error:
            raise ToolPermissionError(
                f"agent has no tool policy and is denied by default: {agent}"
            ) from error

    def authorize(self, agent: str, requested_tools: Iterable[str]) -> None:
        allowed = self.allowed_tools(agent)
        requested = tuple(_identifier(tool, "tool") for tool in requested_tools)
        unauthorized = tuple(tool for tool in requested if tool not in allowed)
        if unauthorized:
            raise ToolPermissionError(
                f"agent {agent} requested unauthorized tools {list(unauthorized)!r}; "
                f"allowed: {list(allowed)!r}"
            )


DEFAULT_AGENT_TOOL_POLICIES = (
    AgentToolPolicy("echo", ()),
    AgentToolPolicy("gateway_research_planner", ("local_document_search",)),
    AgentToolPolicy("gateway_research_reporter", ()),
    AgentToolPolicy("technical_analysis_loop", ("technical_market_analysis",)),
    AgentToolPolicy("fundamental_analysis_loop", ("fundamental_analysis",)),
    AgentToolPolicy("industry_analysis_loop", ("industry_analysis",)),
    AgentToolPolicy("macro_analysis_loop", ("macro_analysis",)),
    AgentToolPolicy("simulation_trader", ()),
    AgentToolPolicy("deterministic_risk_manager", ()),
)


def build_default_agent_tool_policy_registry() -> AgentToolPolicyRegistry:
    """返回当前项目已登记 Agent 的默认最小权限注册表。"""

    return AgentToolPolicyRegistry(DEFAULT_AGENT_TOOL_POLICIES)


@dataclass(frozen=True)
class IndustrialHarnessConfig:
    """运行级保护和工具权限的完整可校验配置。"""

    failure_threshold: int
    reset_timeout_seconds: float
    tool_policies: tuple[AgentToolPolicy, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.failure_threshold, bool)
            or not isinstance(self.failure_threshold, int)
            or self.failure_threshold < 1
        ):
            raise IndustrialHarnessConfigurationError(
                "failure_threshold must be a positive integer"
            )
        if (
            isinstance(self.reset_timeout_seconds, bool)
            or not isinstance(self.reset_timeout_seconds, (int, float))
            or self.reset_timeout_seconds < 0
        ):
            raise IndustrialHarnessConfigurationError(
                "reset_timeout_seconds must be a non-negative number"
            )
        AgentToolPolicyRegistry(self.tool_policies)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IndustrialHarnessConfig":
        if not isinstance(value, Mapping):
            raise IndustrialHarnessConfigurationError("config must be an object")
        required = {"version", "circuit_breaker", "agent_tool_policies"}
        if set(value) != required:
            raise IndustrialHarnessConfigurationError(
                f"config keys must equal {sorted(required)!r}"
            )
        if value["version"] != 1:
            raise IndustrialHarnessConfigurationError(
                "only industrial harness config version 1 is supported"
            )
        breaker = value["circuit_breaker"]
        if not isinstance(breaker, Mapping) or set(breaker) != {
            "failure_threshold",
            "reset_timeout_seconds",
        }:
            raise IndustrialHarnessConfigurationError(
                "circuit_breaker must contain failure_threshold and reset_timeout_seconds"
            )
        raw_policies = value["agent_tool_policies"]
        if not isinstance(raw_policies, list) or not raw_policies:
            raise IndustrialHarnessConfigurationError(
                "agent_tool_policies must be a non-empty array"
            )
        policies: list[AgentToolPolicy] = []
        for item in raw_policies:
            if not isinstance(item, Mapping) or set(item) != {"agent", "allowed_tools"}:
                raise IndustrialHarnessConfigurationError(
                    "each tool policy must contain agent and allowed_tools"
                )
            if not isinstance(item["allowed_tools"], list):
                raise IndustrialHarnessConfigurationError(
                    "allowed_tools must be an array"
                )
            policies.append(
                AgentToolPolicy(
                    agent=item["agent"],
                    allowed_tools=tuple(item["allowed_tools"]),
                )
            )
        return cls(
            failure_threshold=breaker["failure_threshold"],
            reset_timeout_seconds=breaker["reset_timeout_seconds"],
            tool_policies=tuple(policies),
        )


@dataclass(frozen=True)
class IndustrialHarnessEvent:
    event: str
    agent: str
    detail: str = ""


@dataclass(frozen=True)
class HarnessAlert:
    code: str
    agent: str
    severity: str
    message: str
    created_at: datetime


@dataclass(frozen=True)
class IndustrialRunResult:
    value: Any
    status: str
    operation_executed: bool
    trace: tuple[IndustrialHarnessEvent, ...]
    alerts: tuple[HarnessAlert, ...] = ()


class IndustrialHarnessExecutionError(RuntimeError):
    """运行被权限/熔断阻止或执行失败，并保留审计信息。"""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        cause: Exception,
        operation_executed: bool,
        trace: tuple[IndustrialHarnessEvent, ...],
        alerts: tuple[HarnessAlert, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause
        self.operation_executed = operation_executed
        self.trace = trace
        self.alerts = alerts


@dataclass
class _CircuitState:
    state: str = "closed"
    consecutive_failures: int = 0
    opened_at: float | None = None


class IndustrialHarness:
    """用一个 run interface 集中执行权限检查、熔断和告警。"""

    def __init__(
        self,
        config: IndustrialHarnessConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(config, IndustrialHarnessConfig):
            raise IndustrialHarnessConfigurationError(
                "config must be an IndustrialHarnessConfig"
            )
        self._config = config
        self._policies = AgentToolPolicyRegistry(config.tool_policies)
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._circuits: dict[str, _CircuitState] = {}

    @property
    def tool_policies(self) -> AgentToolPolicyRegistry:
        return self._policies

    def run(
        self,
        *,
        agent: str,
        operation: Callable[[], Any],
        requested_tools: Iterable[str] = (),
    ) -> IndustrialRunResult:
        agent = _identifier(agent, "agent")
        if not callable(operation):
            raise IndustrialHarnessConfigurationError("operation must be callable")
        requested_tools = tuple(requested_tools)
        trace: list[IndustrialHarnessEvent] = [
            IndustrialHarnessEvent("industrial_harness.started", agent)
        ]
        alerts: list[HarnessAlert] = []
        circuit = self._circuits.setdefault(agent, _CircuitState())

        if circuit.state == "open":
            assert circuit.opened_at is not None
            elapsed = self._clock() - circuit.opened_at
            if elapsed < self._config.reset_timeout_seconds:
                trace.append(
                    IndustrialHarnessEvent(
                        "circuit.blocked",
                        agent,
                        f"remaining_seconds={self._config.reset_timeout_seconds - elapsed:.3f}",
                    )
                )
                error = RuntimeError(f"circuit is open for agent {agent}")
                raise IndustrialHarnessExecutionError(
                    str(error),
                    code="circuit_open",
                    cause=error,
                    operation_executed=False,
                    trace=tuple(trace),
                )
            circuit.state = "half_open"
            trace.append(IndustrialHarnessEvent("circuit.half_open", agent))
        else:
            trace.append(IndustrialHarnessEvent("circuit.passed", agent))

        try:
            self._policies.authorize(agent, requested_tools)
            trace.append(
                IndustrialHarnessEvent(
                    "tool_permission.passed",
                    agent,
                    f"requested={list(requested_tools)!r}",
                )
            )
        except ToolPermissionError as error:
            trace.append(
                IndustrialHarnessEvent("tool_permission.failed", agent, str(error))
            )
            alerts.extend(self._record_failure(agent, circuit, trace, "tool_permission"))
            raise IndustrialHarnessExecutionError(
                str(error),
                code="tool_permission_denied",
                cause=error,
                operation_executed=False,
                trace=tuple(trace),
                alerts=tuple(alerts),
            ) from error

        trace.append(IndustrialHarnessEvent("operation.started", agent))
        try:
            value = operation()
        except Exception as error:
            trace.append(IndustrialHarnessEvent("operation.failed", agent, str(error)))
            alerts.extend(self._record_failure(agent, circuit, trace, "operation"))
            raise IndustrialHarnessExecutionError(
                str(error),
                code="operation_failed",
                cause=error,
                operation_executed=True,
                trace=tuple(trace),
                alerts=tuple(alerts),
            ) from error

        was_half_open = circuit.state == "half_open"
        circuit.state = "closed"
        circuit.consecutive_failures = 0
        circuit.opened_at = None
        trace.append(IndustrialHarnessEvent("operation.succeeded", agent))
        if was_half_open:
            trace.append(IndustrialHarnessEvent("circuit.closed", agent))
        trace.append(IndustrialHarnessEvent("industrial_harness.succeeded", agent))
        return IndustrialRunResult(
            value=value,
            status="succeeded",
            operation_executed=True,
            trace=tuple(trace),
            alerts=tuple(alerts),
        )

    def circuit_snapshot(self, agent: str) -> dict[str, Any]:
        agent = _identifier(agent, "agent")
        state = self._circuits.get(agent, _CircuitState())
        return {
            "agent": agent,
            "state": state.state,
            "consecutive_failures": state.consecutive_failures,
            "failure_threshold": self._config.failure_threshold,
            "paused": state.state == "open",
        }

    def _record_failure(
        self,
        agent: str,
        circuit: _CircuitState,
        trace: list[IndustrialHarnessEvent],
        source: str,
    ) -> tuple[HarnessAlert, ...]:
        circuit.consecutive_failures += 1
        trace.append(
            IndustrialHarnessEvent(
                "circuit.failure_recorded",
                agent,
                f"count={circuit.consecutive_failures}; source={source}",
            )
        )
        if circuit.consecutive_failures < self._config.failure_threshold:
            return ()
        circuit.state = "open"
        circuit.opened_at = self._clock()
        trace.append(
            IndustrialHarnessEvent(
                "circuit.opened",
                agent,
                f"threshold={self._config.failure_threshold}",
            )
        )
        alert = HarnessAlert(
            code="agent_circuit_opened",
            agent=agent,
            severity="critical",
            message=(
                f"agent {agent} paused after {circuit.consecutive_failures} "
                "consecutive failures"
            ),
            created_at=self._wall_clock(),
        )
        trace.append(
            IndustrialHarnessEvent("alert.emitted", agent, f"code={alert.code}")
        )
        return (alert,)
