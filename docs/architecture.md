# 阶段一架构说明

## 运行关系

```mermaid
flowchart TB
    H[Harness 可靠性与审计层] --> G[Graph 多 Agent 编排层]
    G --> L[Loop 单 Agent 运行层]
    L --> M[Model 与 Tools]
    H --> T[Trace / Guardrail / Human Approval]
```

## 层级职责

- **Model 与 Tools**：提供模型推理和外部能力，不直接决定系统是否接受结果。
- **Loop**：负责一个 Agent 的计划、行动、观察、反思和记忆。
- **Graph**：负责多个 Agent 的节点关系、并行、条件分支和恢复。
- **Harness**：负责输入和输出校验、来源检查、限流、审计、熔断和人工确认。

## 第一周公共接口

```text
AgentRequest ──> Agent.run ──> AgentResponse
      │                              │
      └──── AgentHarness.run ─────────┘
                         │
                    HarnessResult
                  (response + trace)
```

第一周的 Harness 执行顺序固定为：

1. `preflight.started`：开始检查请求；
2. `preflight.passed`：请求和输入 Guardrail 通过；
3. `agent.started` / `agent.finished`：执行 Agent；
4. `postflight.passed`：输出和输出 Guardrail 通过。

任何一步失败都会记录对应的 `*.failed` 事件，并通过 `HarnessExecutionError` 暴露原始异常和已有 trace。

## 第二周 Loop

Loop 不直接调用 Agent，而是持有一个 `AgentHarness`。每次循环都创建带有当前步数和历史响应的请求：

```text
LoopState
   ↓ 生成本步 AgentRequest
AgentHarness.run()
   ↓ 返回 HarnessResult
completion_checker 判断是否完成
   ├── 是：LoopResult
   └── 否：更新 LoopState，进入下一步
```

Loop 的安全边界是 `max_steps` 和 `max_retries`。循环不会无限执行，失败重试耗尽后会保留失败状态和 trace。

## 当前实现边界

现在已经用确定性的 Echo Agent 验证了接口、Harness 和 Loop 闭环。下一步是实现 Graph/DAG，再进入金融数据和专业 Agent。
