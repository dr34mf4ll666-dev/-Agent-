"""第一周的最小 Harness：前置检查、执行、后置检查和 trace。"""

from collections.abc import Iterable

from .contracts import (
    Agent,
    AgentRequest,
    AgentResponse,
    Guardrail,
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

    def run(self, request: AgentRequest) -> HarnessResult:
        """运行一次 Agent；失败时抛出带 trace 的 HarnessExecutionError。"""

        trace: list[TraceEvent] = []
        phase = "preflight"
        self._record(trace, "preflight.started")

        try:
            self._validate_request(request)
            for guardrail in self._guardrails:
                guardrail.check_input(request)
            self._record(trace, "preflight.passed")

            phase = "agent"
            self._record(trace, "agent.started")
            response = self._agent.run(request)
            self._record(trace, "agent.finished")

            phase = "postflight"
            self._validate_response(response)
            for guardrail in self._guardrails:
                guardrail.check_output(response)
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
