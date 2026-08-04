# 认知 Loop 与受控工具

## 这次补上的能力

原有 `LoopRunner` 只负责重复调用同一个 Agent：每步得到响应后，由外部函数判断是否结束。它适合验证最大步数、重试和历史传递，但没有说明 Agent 为什么采取某个动作，也没有真正的工具权限边界。

新增的 `CognitiveLoopRunner` 把一次完整思考拆成四份稳定数据：

1. `Plan`：当前目标和高层步骤；
2. `Action`：本步要调用的工具、参数和理由；
3. `Observation`：工具结果或受控失败；
4. `Reflection`：看到结果后选择 `continue`、`revise` 或 `complete`。

三层记忆不属于本次切片，后续会在这些稳定状态之上增加，因此任务 1.2 仍是进行中。

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

## 当前边界

- 演示 Agent 是确定性 Python 逻辑，不是真实 LLM；
- 工具在当前 Python 进程执行，尚无进程、容器或 worktree 隔离；
- 尚未实现工作记忆、项目记忆和组织记忆；
- 尚未实现 Model Gateway、定时调度、Hook 和递归目标循环。

运行离线演示：

```powershell
python Scripts\demo_cognitive_loop.py
```
