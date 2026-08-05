# 认知 Loop 与受控工具

## 这次补上的能力

原有 `LoopRunner` 只负责重复调用同一个 Agent：每步得到响应后，由外部函数判断是否结束。它适合验证最大步数、重试和历史传递，但没有说明 Agent 为什么采取某个动作，也没有真正的工具权限边界。

新增的 `CognitiveLoopRunner` 把一次完整思考拆成四份稳定数据：

1. `Plan`：当前目标和高层步骤；
2. `Action`：本步要调用的工具、参数和理由；
3. `Observation`：工具结果或受控失败；
4. `Reflection`：看到结果后选择 `continue`、`revise` 或 `complete`。

工作、项目和组织三层记忆已经接入稳定状态与上下文注入，任务 1.2 的完整调度和隔离能力见 [`loop-engineering.md`](loop-engineering.md)。

## 一步是怎样运行的

```text
CognitiveAgent.choose_action()
             ↓ Action
      AgentHarness preflight
             ↓
       ToolRegistry allowlist
             ↓
          Tool.run()
             ↓
      AgentHarness postflight
             ↓ Observation
   CognitiveAgent.reflect()
      ├─ continue：执行下一步
      ├─ revise：修改方案后执行下一步
      └─ complete：生成最终回答
```

`ToolRegistry` 是唯一的工具分发入口。工具名称没有注册时，执行器会抛出 `UnknownToolError`；Harness 将它记录为失败 trace，Loop 再把失败转换为 `Observation.error`。这样 Agent 可以根据真实失败修正动作，但未知工具绝不会被绕过注册表执行。

每个 Action 都会被转换成内部 `AgentRequest`，输入 Guardrail 先检查工具参数。工具成功后，结果放入 `AgentResponse.metadata["observation"]`，输出 Guardrail 再检查结果。只有后置检查通过，Loop 才创建成功的 Observation。

## 重试与安全停止

- `max_tool_retries`：同一个 Action 因工具或 Guardrail 失败时，最多额外尝试几次；
- `max_steps`：Reflection 一直不选择完成时，Loop 的硬停止上限；
- `CognitiveLoopExecutionError`：保存失败时的状态、工具记录、认知 trace 和原始原因；
- `ToolExecutionRecord`：保存每次 Action、最终 Observation、尝试次数和每次 Harness trace。

工具失败在认知层面是一条可反思的 Observation，不会自动冒充成功结果。达到最大步数后则一定安全停止，不会无限循环。

## 工作记忆

Plan 创建后，以及每次 Action、Observation、Reflection 产生后，Loop 都会写入一条简短、JSON 兼容的工作记忆。Agent 在 `choose_action(state)` 和 `reflect(state, observation)` 中通过 `state.memory` 读取当前视图，因此能根据最近一次失败调整后续动作。

工作记忆有固定容量，满后淘汰最旧条目；它不是完整审计日志。完整历史仍在 `CognitiveLoopState`，工作记忆负责给当前任务提供有限的近期上下文。快照、恢复和存储适配器见 [`working-memory.md`](working-memory.md)。

## 当前边界

- 演示 Agent 是确定性 Python 逻辑，不是真实 LLM；
- 工具仍在当前 Python 进程执行；任务文件通过独立工作目录隔离，但没有容器或操作系统权限隔离；
- 长期记忆使用 key、类别和文本筛选，没有向量检索；
- Heartbeat/Cron 使用宿主调用的确定性 tick，没有内置常驻服务；
- 尚未实现 A4 Model Gateway。

运行离线演示：

```powershell
python Scripts\demo_cognitive_loop.py
```
