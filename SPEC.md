# 项目规范（SPEC）

## 1. 项目目标

构建一个可复用的 Agent 平台，并用证券金融分析作为复杂场景验证它的可靠性。平台需要支持：

- 单个 Agent 的多步运行、记忆和验证闭环；
- 多 Agent 的 Graph 编排、并行、条件分支和断点恢复；
- 输出结构化、数据可追溯、行为可约束、过程可审计；
- 在不接入真实下单的前提下完成分析、风控、模拟执行和回测。

## 2. 当前小功能：交付包一验收证据修复

A5 技术能力已经完成，但严格复核发现交付包一仍缺少 dev-map、Skill/MCP/SubAgent 可核查管理证据和 Echo 独立演示。当前小功能只修复这些验收缺口，不提前实现 B1 金融数据 MCP。最终成果、正式任务状态和验收条件以 `ROADMAP.md` 与 `checklist.json` 为准。

### 必须完成

1. 新增根目录 `dev-map.md`，覆盖 SPEC、Rule、Skill、Workflow、Scripts、MCP、SubAgent、dev-map 和任务看板九类组件，并指向真实实现与验证入口。
2. 为 Skill、MCP 和 SubAgents 分别提供版本化可机读 catalog；active 条目必须声明稳定 interface、implementation 和 evidence。
3. 自动化测试必须验证 active catalog 条目的实现符号可以导入，implementation 与 evidence 文件真实存在；pending 条目不得冒充完成。
4. 新增可直接运行的 Echo Agent 演示，通过 `AgentHarness` 输出完整生命周期 trace，并提供 subprocess 自动化测试。
5. 更新 README、目录角色说明、checklist 和 progress，使“交付包一完成”的声明与真实证据一致。
6. 运行完整回归、编译、JSON 解析、离线演示和 Git diff 检查。

### 明确不做

- 不实现或冒充 B1 的真实金融数据 MCP；未经真实返回验证的金融 adapter 保持 pending。
- 不修改 Harness、Loop、Graph、Memory 或 Model Gateway 的核心 interface。
- 不增加网络调用，不消耗真实模型或数据接口额度。
- 不把只有目录或一句未来规划的占位内容当成管理证据。

## 3. 架构原则

```text
Model/Tools → Loop → Graph → Harness
                         ↑
                 Harness 贯穿并校验每一步
```

这里的 `Harness` 不是最后才添加的外壳，而是贯穿输入检查、工具调用、输出验证、日志追踪和人工确认的可靠性层。

## 4. 现有 Echo Agent 与 Harness 接口

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

## 5. 现有 Loop 接口

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

当前重试只针对 Harness 执行失败；达到 `max_steps` 时安全停止，不允许无限循环。

认知闭环使用 `CognitiveLoopRunner`：

```python
runner = CognitiveLoopRunner(
    agent=cognitive_agent,
    tools=ToolRegistry([tool]),
    tool_guardrails=(tool_schema,),
    max_steps=3,
)
result = runner.run(AgentRequest(task="use a controlled tool"))
```

- `Plan`、`Action`、`Observation` 和 `Reflection` 是稳定数据契约。
- `ToolRegistry` 只分发已注册工具，未知工具返回失败 Observation。
- Action 输入和工具输出通过内部 `AgentHarness` 做前后检查。
- `ReflectionDecision` 只能是 `continue`、`revise` 或 `complete`。
- `CognitiveLoopExecutionError` 保留状态、工具记录、Harness trace 和原始原因。

## 6. 现有 Graph 接口

```python
graph = GraphDefinition(
    start="prepare",
    nodes={"prepare": prepare, "finish": finish},
    edges=(GraphEdge(
        "prepare",
        "finish",
        output_schema={"type": "object"},
        input_schema={"type": "object"},
    ),),
    execution=GraphExecutionPolicy(strategy="parallel", max_workers=4),
)
runner = GraphRunner(
    graph,
    checkpoint_store=JsonCheckpointStore("checkpoints/demo.json"),
)
result = runner.run({"request_id": "demo"})
```

- 节点接收只读的 `GraphState`，返回需要合并到状态中的字段映射。
- YAML/JSON 工作流通过 `NodeRegistry` 绑定允许的处理函数，不执行任意配置代码。
- 每条边必须声明 source 输出 Schema 和 target 输入 Schema，传递前自动校验。
- `GraphRunner.run(initial_state)`：从头执行确定性的 DAG。
- `GraphRunner.run(resume=True)`：读取 Checkpoint，从未完成或失败的节点继续。
- `GraphExecutionError`：失败时暴露状态、节点状态、执行顺序和原始异常。
- 支持顺序或并行拓扑波次、确定性合并、节点重试、软超时和持久化熔断。
- `GraphVisualizer` 输出静态或按运行状态着色的 Mermaid。
- 自研接口与 LangGraph 的概念映射见 `docs/langgraph-mapping.md`。

## 7. 阶段二金融数据接口

```python
series = MarketDataSeries.from_records(records)
first_bar = series.bars[0]
first_bar.close      # Decimal("10.20")
first_bar.source     # "synthetic_fixture"
first_bar.as_of      # 行情对应时间
first_bar.timestamp  # 数据获取时间
```

- `MarketBar.from_mapping()`：解析并校验一条外部行情记录。
- `MarketDataSeries.from_records()`：构造同一证券、严格按时间递增的行情序列。
- `MarketDataValidationError`：统一暴露缺失字段、错误格式和不变量错误。
- 当前 fixture 是人工构造的练习数据，不代表任何真实证券或投资结果。

## 8. 阶段二技术分析接口

```python
request = AgentRequest(
    task="analyze the latest technical trend",
    context={"market_data": series},
)
result = AgentHarness(TechnicalAnalysisAgent()).run(request)
analysis = result.response.metadata["analysis"]
```

- 输入必须是 `MarketDataSeries`，并至少包含 20 根 K 线。
- 输出包含 `daily_return`、`sma_5`、`sma_20`、趋势标签和明确的趋势规则。
- 所有指标由确定性 `Decimal` 运算得到，LLM 不参与计算。
- 趋势标签只是简化的技术状态，不是投资建议。

## 9. 最终成功标准摘要

### 平台层

- `AgentHarness` 支持可插拔 Guardrail。
- Loop 支持计划、行动、观察、反思、三层记忆、三类调度和真实模型调用。
- Graph 支持边 Schema、并行、条件边、重试、超时、熔断、Checkpoint 和可视化。
- 一个非金融 Demo 复用同一平台，并能在两天内完成最小接入。

### 应用层

- 技术、基本面、行业、大盘四类 Agent 能输出结构化分析。
- 所有外部数据包含 `source`、`timestamp` 和 `as_of`。
- Trader 和 Risk Manager 输出建议，但不直接执行真实交易。
- 至少 20 只股票跑通端到端分析流程。

### 工程层

- 回测明确区分信号时间与执行时间，并计入交易成本。
- 具备 token、耗时和失败率可观测性，以及失败恢复、质量评估和熔断能力。
- 能用固定实验比较 Harness 对幻觉率、无效调用、成功率、成本和恢复率的影响。

以上只作摘要，完整必做项和验收证据以 `ROADMAP.md` 与 `checklist.json` 为准。

## 10. 当前验收命令

```powershell
python -m unittest discover -s tests -v
```

## 11. 安全底线

- `ALLOW_LIVE_TRADING` 默认必须为 `false`。
- 真实交易能力未列入当前交付范围。
- 数值指标、仓位限制和风险判断必须由确定性代码校验。
- 回测不得使用未来数据；报告要显示数据时间、信号时间和执行时间。

## 12. 下一步

下一项任务是 B1 金融数据 MCP 的最小真实验证：先核对一个真实接口的字段、时间、权限和错误，再决定统一数据工具契约与离线回放方式。本文件将在开始该小功能时收窄为对应的实现边界。
