"""Plan-Action-Observation-Reflection cognitive loop with controlled tools."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .contracts import (
    AgentRequest,
    AgentResponse,
    Guardrail,
    HarnessExecutionError,
    TraceEvent,
)
from .harness import AgentHarness


class CognitiveContractError(ValueError):
    """A cognitive-loop value does not satisfy its public contract."""


class ToolConfigurationError(ValueError):
    """A tool cannot be safely added to the controlled registry."""


class UnknownToolError(LookupError):
    """An action requested a tool outside the controlled registry."""


@dataclass(frozen=True)
class Plan:
    """The current goal and the high-level steps proposed for it."""

    goal: str
    steps: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise CognitiveContractError("plan goal must be a non-empty string")
        normalized_steps = tuple(self.steps)
        if not normalized_steps or any(
            not isinstance(step, str) or not step.strip()
            for step in normalized_steps
        ):
            raise CognitiveContractError(
                "plan steps must contain non-empty strings"
            )
        object.__setattr__(self, "goal", self.goal.strip())
        object.__setattr__(
            self,
            "steps",
            tuple(step.strip() for step in normalized_steps),
        )


@dataclass(frozen=True)
class Action:
    """A single request to one named tool with structured arguments."""

    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.tool, str) or not self.tool.strip():
            raise CognitiveContractError("action tool must be a non-empty string")
        if not isinstance(self.arguments, Mapping):
            raise CognitiveContractError("action arguments must be a mapping")
        if any(not isinstance(key, str) for key in self.arguments):
            raise CognitiveContractError(
                "action argument names must be strings"
            )
        if not isinstance(self.rationale, str):
            raise CognitiveContractError("action rationale must be a string")
        object.__setattr__(self, "tool", self.tool.strip())
        object.__setattr__(self, "arguments", dict(self.arguments))
        object.__setattr__(self, "rationale", self.rationale.strip())


@dataclass(frozen=True)
class Observation:
    """The validated result of a tool attempt, including controlled failures."""

    tool: str
    output: Any = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool, str) or not self.tool.strip():
            raise CognitiveContractError(
                "observation tool must be a non-empty string"
            )
        if self.error is not None and (
            not isinstance(self.error, str) or not self.error.strip()
        ):
            raise CognitiveContractError(
                "observation error must be None or a non-empty string"
            )
        object.__setattr__(self, "tool", self.tool.strip())
        if self.error is not None:
            object.__setattr__(self, "error", self.error.strip())

    @property
    def success(self) -> bool:
        return self.error is None


class ReflectionDecision(str, Enum):
    """The three allowed decisions after observing a tool result."""

    CONTINUE = "continue"
    REVISE = "revise"
    COMPLETE = "complete"


@dataclass(frozen=True)
class Reflection:
    """The agent's explicit decision after inspecting one Observation."""

    decision: ReflectionDecision
    reason: str
    final_answer: str | None = None

    def __post_init__(self) -> None:
        try:
            normalized_decision = ReflectionDecision(self.decision)
        except (TypeError, ValueError) as error:
            raise CognitiveContractError(
                "reflection decision must be continue, revise, or complete"
            ) from error
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise CognitiveContractError(
                "reflection reason must be a non-empty string"
            )
        if normalized_decision is ReflectionDecision.COMPLETE:
            if not isinstance(self.final_answer, str) or not self.final_answer.strip():
                raise CognitiveContractError(
                    "complete reflection requires a non-empty final_answer"
                )
        elif self.final_answer is not None:
            raise CognitiveContractError(
                "only a complete reflection may contain final_answer"
            )
        object.__setattr__(self, "decision", normalized_decision)
        object.__setattr__(self, "reason", self.reason.strip())
        if self.final_answer is not None:
            object.__setattr__(self, "final_answer", self.final_answer.strip())


class Tool(Protocol):
    """Minimal adapter contract for a controlled capability."""

    name: str

    def run(self, arguments: Mapping[str, Any]) -> Any:
        """Execute the capability with structured arguments."""


class CognitiveAgent(Protocol):
    """Reasoning seam; deterministic and future LLM adapters share this shape."""

    name: str

    def create_plan(self, request: AgentRequest) -> Plan:
        """Create the initial plan for one request."""

    def choose_action(self, state: CognitiveLoopState) -> Action:
        """Choose one controlled tool action from current state."""

    def reflect(
        self,
        state: CognitiveLoopState,
        observation: Observation,
    ) -> Reflection:
        """Decide whether to continue, revise, or complete."""


class ToolRegistry:
    """Allowlist and dispatch point for all executable tools."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise ToolConfigurationError("tool name must be a non-empty string")
        name = name.strip()
        if name in self._tools:
            raise ToolConfigurationError(f"duplicate tool name: {name}")
        if not callable(getattr(tool, "run", None)):
            raise ToolConfigurationError(f"tool {name} must define run")
        self._tools[name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def execute(self, name: str, arguments: Mapping[str, Any]) -> Any:
        try:
            tool = self._tools[name]
        except KeyError as error:
            raise UnknownToolError(
                f"tool is not registered: {name}; allowed: {list(self.names)!r}"
            ) from error
        return tool.run(arguments)


@dataclass(frozen=True)
class CognitiveLoopState:
    """Immutable accumulated state of the cognitive loop."""

    request: AgentRequest
    plan: Plan | None = None
    step_count: int = 0
    actions: tuple[Action, ...] = ()
    observations: tuple[Observation, ...] = ()
    reflections: tuple[Reflection, ...] = ()
    done: bool = False

    def with_plan(self, plan: Plan) -> CognitiveLoopState:
        return CognitiveLoopState(request=self.request, plan=plan)

    def record_action(self, action: Action) -> CognitiveLoopState:
        return CognitiveLoopState(
            request=self.request,
            plan=self.plan,
            step_count=self.step_count,
            actions=(*self.actions, action),
            observations=self.observations,
            reflections=self.reflections,
            done=self.done,
        )

    def record_observation(self, observation: Observation) -> CognitiveLoopState:
        return CognitiveLoopState(
            request=self.request,
            plan=self.plan,
            step_count=self.step_count,
            actions=self.actions,
            observations=(*self.observations, observation),
            reflections=self.reflections,
            done=self.done,
        )

    def record_reflection(self, reflection: Reflection) -> CognitiveLoopState:
        done = reflection.decision is ReflectionDecision.COMPLETE
        return CognitiveLoopState(
            request=self.request,
            plan=self.plan,
            step_count=self.step_count + 1,
            actions=self.actions,
            observations=self.observations,
            reflections=(*self.reflections, reflection),
            done=done,
        )


@dataclass(frozen=True)
class CognitiveLoopEvent:
    event: str
    step: int
    attempt: int = 0
    detail: str = ""


@dataclass(frozen=True)
class ToolExecutionRecord:
    action: Action
    observation: Observation
    attempts: int
    harness_traces: tuple[tuple[TraceEvent, ...], ...]


@dataclass(frozen=True)
class CognitiveLoopResult:
    response: AgentResponse
    state: CognitiveLoopState
    tool_records: tuple[ToolExecutionRecord, ...]
    trace: tuple[CognitiveLoopEvent, ...]


class CognitiveMaxStepsExceeded(RuntimeError):
    """The loop reached its hard step limit without completion."""


class CognitiveLoopExecutionError(RuntimeError):
    """A failed loop with all state and traces collected before the failure."""

    def __init__(
        self,
        message: str,
        *,
        state: CognitiveLoopState,
        tool_records: tuple[ToolExecutionRecord, ...],
        trace: tuple[CognitiveLoopEvent, ...],
        cause: Exception,
    ) -> None:
        super().__init__(message)
        self.state = state
        self.tool_records = tool_records
        self.trace = trace
        self.cause = cause


class _ControlledToolAgent:
    name = "controlled_tool_executor"

    def __init__(self, tools: ToolRegistry) -> None:
        self._tools = tools

    def run(self, request: AgentRequest) -> AgentResponse:
        tool_name = request.context.get("tool")
        arguments = request.context.get("arguments")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise CognitiveContractError(
                "controlled tool request requires a tool name"
            )
        if not isinstance(arguments, Mapping):
            raise CognitiveContractError(
                "controlled tool request requires argument mapping"
            )
        output = self._tools.execute(tool_name, arguments)
        return AgentResponse(
            content=f"tool {tool_name} completed",
            metadata={
                "observation": {
                    "tool": tool_name,
                    "output": output,
                }
            },
        )


class CognitiveLoopRunner:
    """Run a finite Plan-Action-Observation-Reflection loop."""

    def __init__(
        self,
        *,
        agent: CognitiveAgent,
        tools: ToolRegistry,
        tool_guardrails: Iterable[Guardrail] = (),
        max_steps: int = 5,
        max_tool_retries: int = 0,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_tool_retries < 0:
            raise ValueError("max_tool_retries must not be negative")
        for method_name in ("create_plan", "choose_action", "reflect"):
            if not callable(getattr(agent, method_name, None)):
                raise CognitiveContractError(
                    f"cognitive agent must define {method_name}"
                )
        self._agent = agent
        self._tool_harness = AgentHarness(
            _ControlledToolAgent(tools),
            guardrails=tool_guardrails,
        )
        self._max_steps = max_steps
        self._max_tool_retries = max_tool_retries

    def run(self, request: AgentRequest) -> CognitiveLoopResult:
        state = CognitiveLoopState(request=request)
        records: list[ToolExecutionRecord] = []
        current_step = 0
        trace: list[CognitiveLoopEvent] = [
            CognitiveLoopEvent(event="cognitive_loop.started", step=0)
        ]

        try:
            plan = self._agent.create_plan(request)
            if not isinstance(plan, Plan):
                raise CognitiveContractError("create_plan must return Plan")
            state = state.with_plan(plan)
            trace.append(
                CognitiveLoopEvent(event="cognitive_loop.plan.created", step=0)
            )

            while state.step_count < self._max_steps:
                step = state.step_count + 1
                current_step = step
                trace.append(
                    CognitiveLoopEvent(
                        event="cognitive_loop.step.started",
                        step=step,
                    )
                )
                action = self._agent.choose_action(state)
                if not isinstance(action, Action):
                    raise CognitiveContractError("choose_action must return Action")
                state = state.record_action(action)
                trace.append(
                    CognitiveLoopEvent(
                        event="cognitive_loop.action.selected",
                        step=step,
                        detail=action.tool,
                    )
                )

                observation, record = self._execute_action(action, step, trace)
                records.append(record)
                state = state.record_observation(observation)
                trace.append(
                    CognitiveLoopEvent(
                        event="cognitive_loop.observation.recorded",
                        step=step,
                        detail="success" if observation.success else observation.error or "",
                    )
                )

                reflection = self._agent.reflect(state, observation)
                if not isinstance(reflection, Reflection):
                    raise CognitiveContractError("reflect must return Reflection")
                state = state.record_reflection(reflection)
                trace.append(
                    CognitiveLoopEvent(
                        event="cognitive_loop.reflection.recorded",
                        step=step,
                        detail=reflection.decision.value,
                    )
                )

                if reflection.decision is ReflectionDecision.COMPLETE:
                    trace.append(
                        CognitiveLoopEvent(
                            event="cognitive_loop.completed",
                            step=step,
                        )
                    )
                    response = AgentResponse(
                        content=reflection.final_answer or "",
                        metadata={
                            "cognitive_loop": {
                                "steps": state.step_count,
                                "goal": plan.goal,
                                "tools": [item.action.tool for item in records],
                            }
                        },
                    )
                    return CognitiveLoopResult(
                        response=response,
                        state=state,
                        tool_records=tuple(records),
                        trace=tuple(trace),
                    )

            cause = CognitiveMaxStepsExceeded(
                f"cognitive loop did not complete within {self._max_steps} steps"
            )
            trace.append(
                CognitiveLoopEvent(
                    event="cognitive_loop.max_steps_exceeded",
                    step=state.step_count,
                    detail=str(cause),
                )
            )
            self._raise_execution_error(
                message=str(cause),
                state=state,
                records=records,
                trace=trace,
                cause=cause,
            )
        except CognitiveLoopExecutionError:
            raise
        except Exception as error:
            trace.append(
                CognitiveLoopEvent(
                    event="cognitive_loop.failed",
                    step=current_step,
                    detail=str(error),
                )
            )
            self._raise_execution_error(
                message=f"cognitive loop failed: {error}",
                state=state,
                records=records,
                trace=trace,
                cause=error,
            )

    def _execute_action(
        self,
        action: Action,
        step: int,
        trace: list[CognitiveLoopEvent],
    ) -> tuple[Observation, ToolExecutionRecord]:
        harness_traces: list[tuple[TraceEvent, ...]] = []
        attempt = 0
        while attempt <= self._max_tool_retries:
            attempt += 1
            trace.append(
                CognitiveLoopEvent(
                    event="cognitive_loop.tool.started",
                    step=step,
                    attempt=attempt,
                    detail=action.tool,
                )
            )
            request = AgentRequest(
                task=f"execute controlled tool: {action.tool}",
                context={
                    "tool": action.tool,
                    "arguments": dict(action.arguments),
                },
            )
            try:
                result = self._tool_harness.run(request)
                harness_traces.append(result.trace)
                payload = result.response.metadata["observation"]
                observation = Observation(
                    tool=payload["tool"],
                    output=payload["output"],
                )
                trace.append(
                    CognitiveLoopEvent(
                        event="cognitive_loop.tool.finished",
                        step=step,
                        attempt=attempt,
                        detail=action.tool,
                    )
                )
                return observation, ToolExecutionRecord(
                    action=action,
                    observation=observation,
                    attempts=attempt,
                    harness_traces=tuple(harness_traces),
                )
            except HarnessExecutionError as error:
                harness_traces.append(error.trace)
                if attempt <= self._max_tool_retries:
                    trace.append(
                        CognitiveLoopEvent(
                            event="cognitive_loop.tool.retry",
                            step=step,
                            attempt=attempt,
                            detail=str(error.cause),
                        )
                    )
                    continue
                error_detail = f"{type(error.cause).__name__}: {error.cause}"
                observation = Observation(tool=action.tool, error=error_detail)
                trace.append(
                    CognitiveLoopEvent(
                        event="cognitive_loop.tool.failed",
                        step=step,
                        attempt=attempt,
                        detail=error_detail,
                    )
                )
                return observation, ToolExecutionRecord(
                    action=action,
                    observation=observation,
                    attempts=attempt,
                    harness_traces=tuple(harness_traces),
                )

        raise AssertionError("unreachable tool attempt state")

    @staticmethod
    def _raise_execution_error(
        *,
        message: str,
        state: CognitiveLoopState,
        records: list[ToolExecutionRecord],
        trace: list[CognitiveLoopEvent],
        cause: Exception,
    ) -> None:
        raise CognitiveLoopExecutionError(
            message,
            state=state,
            tool_records=tuple(records),
            trace=tuple(trace),
            cause=cause,
        ) from cause
