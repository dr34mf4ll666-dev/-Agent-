"""第二周的最小 Loop：状态、完成条件、步数限制和重试。"""

from collections.abc import Callable
from dataclasses import dataclass

from .contracts import (
    AgentRequest,
    AgentResponse,
    HarnessExecutionError,
    HarnessResult,
)
from .harness import AgentHarness


CompletionChecker = Callable[[AgentResponse], bool]


@dataclass(frozen=True)
class LoopState:
    """Loop 在某一时刻的任务状态。"""

    request: AgentRequest
    step_count: int = 0
    history: tuple[AgentResponse, ...] = ()
    done: bool = False

    def advance(self, response: AgentResponse, done: bool) -> "LoopState":
        """用本次结果生成下一份不可变状态。"""

        return LoopState(
            request=self.request,
            step_count=self.step_count + 1,
            history=(*self.history, response),
            done=done,
        )


@dataclass(frozen=True)
class LoopEvent:
    """Loop 自身的生命周期事件。Harness trace 保存在每个 step_result 中。"""

    event: str
    step: int
    attempt: int = 0
    detail: str = ""


@dataclass(frozen=True)
class LoopResult:
    """Loop 成功完成后的最终结果、状态、每步 Harness 结果和事件。"""

    response: AgentResponse
    state: LoopState
    step_results: tuple[HarnessResult, ...]
    trace: tuple[LoopEvent, ...]


class LoopMaxStepsExceeded(RuntimeError):
    """Loop 在规定步数内没有满足完成条件。"""


class LoopExecutionError(RuntimeError):
    """Loop 失败，并保留状态、已经完成的步骤和原始异常。"""

    def __init__(
        self,
        message: str,
        state: LoopState,
        step_results: tuple[HarnessResult, ...],
        trace: tuple[LoopEvent, ...],
        cause: Exception,
    ) -> None:
        super().__init__(message)
        self.state = state
        self.step_results = step_results
        self.trace = trace
        self.cause = cause


class LoopRunner:
    """让同一个 Agent 在 Harness 保护下重复执行有限步。"""

    def __init__(
        self,
        harness: AgentHarness,
        *,
        completion_checker: CompletionChecker,
        max_steps: int = 5,
        max_retries: int = 0,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")

        self._harness = harness
        self._completion_checker = completion_checker
        self._max_steps = max_steps
        self._max_retries = max_retries

    def run(self, request: AgentRequest) -> LoopResult:
        """运行 Loop，直到完成、失败或达到最大步数。"""

        state = LoopState(request=request)
        step_results: list[HarnessResult] = []
        trace: list[LoopEvent] = [LoopEvent(event="loop.started", step=0)]

        while state.step_count < self._max_steps:
            step_number = state.step_count + 1
            attempt = 0

            while attempt <= self._max_retries:
                attempt += 1
                trace.append(
                    LoopEvent(
                        event="loop.step.started",
                        step=step_number,
                        attempt=attempt,
                    )
                )

                try:
                    step_request = self._request_for(state)
                    harness_result = self._harness.run(step_request)
                    done = self._completion_checker(harness_result.response)
                    state = state.advance(harness_result.response, done)
                    step_results.append(harness_result)
                    trace.append(
                        LoopEvent(
                            event="loop.step.finished",
                            step=step_number,
                            attempt=attempt,
                        )
                    )

                    if done:
                        trace.append(
                            LoopEvent(
                                event="loop.completed",
                                step=step_number,
                                attempt=attempt,
                            )
                        )
                        return LoopResult(
                            response=harness_result.response,
                            state=state,
                            step_results=tuple(step_results),
                            trace=tuple(trace),
                        )
                    break
                except HarnessExecutionError as error:
                    if attempt <= self._max_retries:
                        trace.append(
                            LoopEvent(
                                event="loop.retry",
                                step=step_number,
                                attempt=attempt,
                                detail=str(error),
                            )
                        )
                        continue

                    trace.append(
                        LoopEvent(
                            event="loop.failed",
                            step=step_number,
                            attempt=attempt,
                            detail=str(error),
                        )
                    )
                    self._raise_execution_error(
                        message=f"loop failed at step {step_number}",
                        state=state,
                        step_results=step_results,
                        trace=trace,
                        cause=error,
                    )

        cause = LoopMaxStepsExceeded(
            f"loop did not complete within {self._max_steps} steps"
        )
        trace.append(
            LoopEvent(
                event="loop.max_steps_exceeded",
                step=state.step_count,
                detail=str(cause),
            )
        )
        self._raise_execution_error(
            message=str(cause),
            state=state,
            step_results=step_results,
            trace=trace,
            cause=cause,
        )

    def _request_for(self, state: LoopState) -> AgentRequest:
        context = dict(state.request.context)
        context["step"] = state.step_count + 1
        context["history"] = state.history
        return AgentRequest(task=state.request.task, context=context)

    @staticmethod
    def _raise_execution_error(
        *,
        message: str,
        state: LoopState,
        step_results: list[HarnessResult],
        trace: list[LoopEvent],
        cause: Exception,
    ) -> None:
        raise LoopExecutionError(
            message=message,
            state=state,
            step_results=tuple(step_results),
            trace=tuple(trace),
            cause=cause,
        ) from cause
