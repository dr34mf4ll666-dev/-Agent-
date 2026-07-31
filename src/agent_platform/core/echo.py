"""用于验证 Agent/Harness 闭环的确定性 Echo Agent。"""

from .contracts import AgentRequest, AgentResponse


class EchoAgent:
    """把任务原样返回，不依赖模型、网络或外部工具。"""

    name = "echo"

    def run(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            content=request.task,
            metadata={"agent": self.name},
        )
