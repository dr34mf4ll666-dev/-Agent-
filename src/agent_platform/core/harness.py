"""第一周的最小 Harness：前置检查、执行、后置检查和 trace。"""

from collections.abc import Iterable

from .contracts import (
    Agent,
    AgentRequest,
    AgentResponse,
    Guardrail,
    GuardrailConfigurationError,
    GuardrailViolation,
    HarnessExecutionError,
    HarnessResult,
    TraceEvent,
)


class AgentHarness:
    """在一个窄接口后封装 Agent 的检查和追踪流程。"""

    def __init__(self, agent: Agent, guardrails: Iterable[Guardrail] = ()) -> None:
        self._agent = agent
        self._guardrails = tuple(guardrails)
        self._validate_guardrails(self._guardrails)

    def run(self, request: AgentRequest) -> HarnessResult:
        """运行一次 Agent；失败时抛出带 trace 的 HarnessExecutionError。"""

        trace: list[TraceEvent] = []
        phase = "preflight"
        self._record(trace, "preflight.started")

        try:
            self._validate_request(request)
            for guardrail in self._guardrails:
                self._run_guardrail(
                    guardrail=guardrail,
                    stage="input",
                    value=request,
                    trace=trace,
                )
            self._record(trace, "preflight.passed")

            phase = "agent"
            self._record(trace, "agent.started")
            response = self._agent.run(request)
            self._record(trace, "agent.finished")

            phase = "postflight"
            self._validate_response(response)
            for guardrail in self._guardrails:
                self._run_guardrail(
                    guardrail=guardrail,
                    stage="output",
                    value=response,
                    trace=trace,
                )
            self._record(trace, "postflight.passed")

            return HarnessResult(response=response, trace=tuple(trace))
        except Exception as error:
            failure_event = f"{phase}.failed"
            self._record(trace, failure_event, detail=str(error))
            error_trace = tuple(trace)
            raise HarnessExecutionError(
                message=f"{phase} failed: {error}",
                trace=error_trace,
                cause=error,
            ) from error

    @property
    def agent_name(self) -> str:
        """返回被包裹 Agent 的稳定名称，便于日志和后续观测。"""

        return self._agent.name

    def _record(
        self,
        trace: list[TraceEvent],
        event: str,
        detail: str = "",
    ) -> None:
        trace.append(TraceEvent(event=event, agent=self.agent_name, detail=detail))

    def _run_guardrail(
        self,
        *,
        guardrail: Guardrail,
        stage: str,
        value: AgentRequest | AgentResponse,
        trace: list[TraceEvent],
    ) -> None:
        event_prefix = f"guardrail.{stage}"
        self._record(trace, f"{event_prefix}.started", detail=guardrail.name)
        try:
            if stage == "input":
                guardrail.check_input(value)  # type: ignore[arg-type]
            else:
                guardrail.check_output(value)  # type: ignore[arg-type]
        except Exception as error:
            self._record(
                trace,
                f"{event_prefix}.failed",
                detail=f"{guardrail.name}: {error}",
            )
            raise
        self._record(trace, f"{event_prefix}.passed", detail=guardrail.name)

    @staticmethod
    def _validate_guardrails(guardrails: tuple[Guardrail, ...]) -> None:
        names: set[str] = set()
        for guardrail in guardrails:
            name = getattr(guardrail, "name", None)
            if not isinstance(name, str) or not name.strip():
                raise GuardrailConfigurationError(
                    "guardrail name must be a non-empty string"
                )
            if name in names:
                raise GuardrailConfigurationError(
                    f"guardrail names must be unique; duplicate: {name}"
                )
            if not callable(getattr(guardrail, "check_input", None)):
                raise GuardrailConfigurationError(
                    f"guardrail {name} must define check_input"
                )
            if not callable(getattr(guardrail, "check_output", None)):
                raise GuardrailConfigurationError(
                    f"guardrail {name} must define check_output"
                )
            names.add(name)

    @staticmethod
    def _validate_request(request: AgentRequest) -> None:
        if not isinstance(request, AgentRequest):
            raise GuardrailViolation("request must be an AgentRequest")
        if not request.task.strip():
            raise GuardrailViolation("task must not be blank")

    @staticmethod
    def _validate_response(response: AgentResponse) -> None:
        if not isinstance(response, AgentResponse):
            raise GuardrailViolation("agent must return an AgentResponse")
        if not response.content.strip():
            raise GuardrailViolation("response content must not be blank")
