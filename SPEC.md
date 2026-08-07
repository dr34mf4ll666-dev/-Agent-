# 项目规范（SPEC）

## 1. 项目目标

构建一个可复用的 Agent 平台，并用证券金融分析作为复杂场景验证它的可靠性。平台需要支持：

- 单个 Agent 的多步运行、记忆和验证闭环；
- 多 Agent 的 Graph 编排、并行、条件分支和断点恢复；
- 输出结构化、数据可追溯、行为可约束、过程可审计；
- 在不接入真实下单的前提下完成分析、风控、模拟执行和回测。

## 2. 已完成功能：B1 完整金融 Data Hub 与 MCP

本轮已按 `ROADMAP.md` 的完整 B1 口径，把行情、财务、宏观行业和舆情数据收敛到一个统一 Data Hub，并通过官方 MCP Python SDK 暴露只读工具。AKShare、腾讯和 Tushare 均已留下真实成功证据；认证失败、权限失败和离线回放契约也有自动化测试。

### 验收内容（全部完成）

1. `FinancialDataHub.fetch(dataset, params, mode)` 覆盖日线、周线、分钟线、实时报价、资金流、三大报表、财务指标、PE/PB/PS、指数、行业、GDP、Shibor、LPR、新闻、公告和研报。
2. 每条记录统一包含 `subject`、`fields`、`source`、`timestamp` 和 `as_of`；金融数值以十进制字符串跨 JSON 传递，确定性计算使用 `Decimal`。
3. 真实 provider 在可终止子进程运行，实现整个调用的硬总超时，并统一提供缓存、provider 限流、有限重试、错误码和 trace。
4. 默认离线 fixture 覆盖全部 dataset；19 个 dataset 均使用经过最小真实验证的样本，不再包含 synthetic Tushare 数据。
5. 使用官方 MCP Python SDK 注册 `list_financial_datasets` 和 `get_financial_data`，默认 stdio、默认离线且只读。
6. 对 AKShare/腾讯可用数据集逐类执行最小真实整链验证；东方财富被限制时记录失败并切换其他来源，不高频重复请求。
7. Tushare 只从 `TUSHARE_TOKEN` 环境变量读取凭证；没有 token 时返回 `auth_required`，token 有效但账号权限不足时返回 `permission_denied`。权限生效后日线真实调用成功返回 4 条记录，离线 fixture 已替换为真实样本。
8. 更新 README、MCP catalog、dev-map、checklist 和 progress，运行定向测试、完整回归、编译、JSON 解析、离线演示和 Git diff 检查。

### 明确不做

- 不支持前复权或后复权；现有 `MarketBar` 要求价格为正，而 AKShare 官方说明复权历史价可能为负，二者需要后续单独设计。
- 不修改 Harness、Loop、Graph、Memory 或 Model Gateway 的核心 interface。
- 不在 B1 实现技术面、基本面、行业或大盘分析 Agent；这些属于 B2。
- 验收过程中没有把 Tushare 的认证失败、权限失败或 synthetic fixture 当作真实成功；只有日线真实调用成功后才把 B1 标记为 `done`。

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

B1 已完成。Tushare 日线成功返回 4 条真实记录，最后一个 synthetic fixture 已替换；19 个 dataset 均有真实最小样本和离线回放。下一步进入 B2 四类专业 Agent，先让现有技术分析原型改为消费统一 Data Hub，并按 ROADMAP 补齐完整指标、Loop、Harness、测试和样例报告。
