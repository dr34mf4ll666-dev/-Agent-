"""第一周的公共契约：Agent 输入、输出、追踪和 Guardrail 接口。"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class AgentRequest:
    """一次 Agent 执行请求。"""

    task: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResponse:
    """Agent 返回给 Harness 的结构化结果。"""

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Agent(Protocol):
    """所有 Agent 适配器都需要满足的最小接口。"""

    name: str

    def run(self, request: AgentRequest) -> AgentResponse:
        """处理一次请求并返回结构化结果。"""


class Guardrail(Protocol):
    """可插拔的输入和输出检查接口。"""

    name: str

    def check_input(self, request: AgentRequest) -> None:
        """输入不符合规则时抛出 GuardrailViolation。"""

    def check_output(self, response: AgentResponse) -> None:
        """输出不符合规则时抛出 GuardrailViolation。"""


@dataclass(frozen=True)
class TraceEvent:
    """一次 Harness 生命周期事件。"""

    event: str
    agent: str
    detail: str = ""


@dataclass(frozen=True)
class HarnessResult:
    """成功执行后的结果和有序追踪记录。"""

    response: AgentResponse
    trace: tuple[TraceEvent, ...]


class GuardrailViolation(ValueError):
    """输入或输出违反了 Harness 规则。"""


class HarnessExecutionError(RuntimeError):
    """Harness 执行失败，并保留失败前的 trace 和原始异常。"""

    def __init__(
        self,
        message: str,
        trace: tuple[TraceEvent, ...],
        cause: Exception,
    ) -> None:
        super().__init__(message)
        self.trace = trace
        self.cause = cause
