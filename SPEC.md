# 项目规范（SPEC）

## 1. 项目目标

构建一个可复用的 Agent 平台，并用证券金融分析作为复杂场景验证它的可靠性。平台需要支持：

- 单个 Agent 的多步运行、记忆和验证闭环；
- 多 Agent 的 Graph 编排、并行、条件分支和断点恢复；
- 输出结构化、数据可追溯、行为可约束、过程可审计；
- 在不接入真实下单的前提下完成分析、风控、模拟执行和回测。

## 2. 当前阶段：对齐修正后的第 5 周（任务 1.4）

现有 Harness、Loop、Graph 和金融模块都是可运行的阶段性原型，但阶段一尚未通过任务书整体验收。当前先补齐 Harness SDK 要求的五类 Guardrail，再继续扩展金融流程。正式任务状态和阶段门槛以 `ROADMAP.md` 与 `checklist.json` 为准。

### 必须完成

1. 实现 `JSONSchemaValidator`，校验指定的输入或输出结构。
2. 实现 `SourceAttributionFilter`，拒绝缺少来源与时间字段的外部事实。
3. 实现 `RateLimiter`，用确定性规则限制指定窗口内的调用次数。
4. 实现 `KeywordBlocker`，阻断配置中的敏感或禁止关键词。
5. 实现 `CrossValidator`，支持两个独立结果的一致性校验。
6. 五类 Guardrail 均通过统一 Harness 入口运行，并覆盖允许、拒绝和 trace 测试。
7. 提供最小配置和演示，说明每类 Guardrail 在什么阶段生效。

### 明确不做

- 不在本步接入 AKShare、Tushare、券商接口或真实 LLM。
- 不扩展新的金融指标、专业 Agent 或金融 Graph。
- 不补做 Loop 和 Graph 的剩余能力；它们按总纲在后续小步完成。
- 不接入数据库、分布式限流或真实交易能力。
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

## 9. 后续阶段的成功标准

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

## 10. 当前验收命令

```powershell
python -m unittest discover -s tests -v
```

## 11. 安全底线

- `ALLOW_LIVE_TRADING` 默认必须为 `false`。
- 真实交易能力未列入当前交付范围。
- 数值指标、仓位限制和风险判断必须由确定性代码校验。
- 回测不得使用未来数据；报告要显示数据时间、信号时间和执行时间。
