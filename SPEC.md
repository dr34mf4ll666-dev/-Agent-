# 项目规范（SPEC）

## 1. 项目目标

构建一个可复用的 Agent 平台，并用证券金融分析作为复杂场景验证它的可靠性。平台需要支持：

- 单个 Agent 的多步运行、记忆和验证闭环；
- 多 Agent 的 Graph 编排、并行、条件分支和断点恢复；
- 输出结构化、数据可追溯、行为可约束、过程可审计；
- 在不接入真实下单的前提下完成分析、风控、模拟执行和回测。

## 2. 当前阶段：阶段一第三周

第 0 周的项目入口、第一周的 Echo/Harness 闭环和第二周的 Loop 已经完成。本周实现一个离线、确定性的 Graph/DAG 运行器，并加入最小 Checkpoint 恢复能力。

### 必须完成

1. 定义 Graph 状态、节点、边和运行结果的稳定契约。
2. 在执行前校验入口、边端点和 DAG 无环性。
3. 按拓扑顺序执行节点，并合并节点返回的状态更新。
4. 支持条件边和未选中分支的跳过传播。
5. 将执行状态保存到 JSON Checkpoint，失败后可以继续执行。

### 明确不做

- 不在本周接入 AKShare、Tushare 或券商接口。
- 不在本周接入真实 LLM 或实现复杂的计划器、长期记忆。
- 不在本周实现真正的并行调度、分布式执行或超时控制。
- 不在本周实现 YAML/JSON 工作流解析器和图形化编辑器。
- 不把 API 密钥或账户信息写入项目。

## 3. 架构原则

```text
Model/Tools → Loop → Graph → Harness
                         ↑
                 Harness 贯穿并校验每一步
```

这里的 `Harness` 不是最后才添加的外壳，而是贯穿输入检查、工具调用、输出验证、日志追踪和人工确认的可靠性层。

## 4. 第一周公共接口

调用方只需要知道三个接口事实：

```python
request = AgentRequest(task="hello")
result = AgentHarness(EchoAgent()).run(request)
result.response.content  # "hello"
result.trace             # 有序的生命周期事件
```

- `Agent.run(request) -> AgentResponse`：Agent 的最小执行接口。
- `AgentHarness.run(request) -> HarnessResult`：统一的可靠性入口。
- `HarnessExecutionError.trace`：失败时读取已经发生的事件，不需要访问 Harness 内部状态。

当前实现不承诺持久化时间戳、分布式调度或长期记忆；这些属于后续 Graph 和工程化阶段。

## 5. 第二周公共接口

```python
runner = LoopRunner(
    AgentHarness(agent),
    completion_checker=lambda response: response.metadata.get("done", False),
    max_steps=3,
    max_retries=1,
)
result = runner.run(AgentRequest(task="complete the task"))
```

- `LoopState`：保存当前请求、已执行步数、响应历史和是否完成。
- `LoopRunner.run()`：重复执行有限步，并把每步交给 Harness。
- `completion_checker`：由调用方决定什么结果算完成。
- `LoopResult`：返回最终响应、最终状态、每步 Harness 结果和 Loop trace。
- `LoopExecutionError`：失败时暴露失败状态、已经完成的步骤和原始异常。

第二周的重试只针对 Harness 执行失败；达到 `max_steps` 时安全停止，不允许无限循环。

## 6. 第三周公共接口

```python
graph = GraphDefinition(
    start="prepare",
    nodes={"prepare": prepare, "finish": finish},
    edges=(GraphEdge("prepare", "finish"),),
)
runner = GraphRunner(
    graph,
    checkpoint_store=JsonCheckpointStore("checkpoints/demo.json"),
)
result = runner.run({"request_id": "demo"})
```

- 节点接收只读的 `GraphState`，返回需要合并到状态中的字段映射。
- `GraphRunner.run(initial_state)`：从头执行确定性的 DAG。
- `GraphRunner.run(resume=True)`：读取 Checkpoint，从未完成或失败的节点继续。
- `GraphExecutionError`：失败时暴露状态、节点状态、执行顺序和原始异常。
- 当前实现为单进程顺序执行；拓扑排序保证依赖顺序，但不承诺并行。

## 7. 后续阶段的成功标准

### 平台层

- `AgentHarness` 支持可插拔 Guardrail。
- Loop 支持计划、行动、观察、反思和记忆。
- Graph 支持并行、条件边、重试、超时和 Checkpoint。
- 非金融 demo 可以在两天内接入平台。

### 应用层

- 技术、基本面、行业、大盘四类 Agent 能输出结构化分析。
- 所有外部数据包含 `source`、`timestamp` 和 `as_of`。
- Trader 和 Risk Manager 输出建议，但不直接执行真实交易。
- 至少 20 只股票跑通端到端分析流程。

### 工程层

- 回测明确区分信号时间与执行时间，并计入交易成本。
- 具备日志追踪、失败恢复、质量评估和熔断能力。
- 能用实验数据比较 Harness 对幻觉率、无效调用和成功率的影响。

## 8. 当前验收命令

```powershell
python -m unittest discover -s tests -v
```

## 9. 安全底线

- `ALLOW_LIVE_TRADING` 默认必须为 `false`。
- 真实交易能力未列入当前交付范围。
- 数值指标、仓位限制和风险判断必须由确定性代码校验。
- 回测不得使用未来数据；报告要显示数据时间、信号时间和执行时间。
