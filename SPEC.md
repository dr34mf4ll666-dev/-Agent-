# 项目规范（SPEC）

## 1. 项目目标

构建一个可复用的 Agent 平台，并用证券金融分析作为复杂场景验证它的可靠性。平台需要支持：

- 单个 Agent 的多步运行、记忆和验证闭环；
- 多 Agent 的 Graph 编排、并行、条件分支和断点恢复；
- 输出结构化、数据可追溯、行为可约束、过程可审计；
- 在不接入真实下单的前提下完成分析、风控、模拟执行和回测。

## 2. 当前小功能：D1 回测系统完成

D1 使用两个深 module：`BacktestEngine` 负责单股票确定性执行，`BacktestExperimentRunner` 负责固定股票池、C3 报告适配、组合和基准。调用方提交带时间和来源的行情、Agent 决策及市场事件，module 内部统一完成时间门禁、撮合、成本、公司行为、权益曲线和绩效。

### 正式验收内容

1. `signal_at` 必须绑定已完成 K 线，`available_at` 记录 Agent 真正生成时间；信号晚于下一开盘时自动延后，禁止同 K 线成交。
2. `C3DecisionSnapshot` 校验每条证据的 `source`、`as_of` 和 `available_at`，未来证据直接拒绝；C3 报告重新执行确定性交叉验证。
3. 下一可交易日 09:30 按开盘价撮合；成交量为 0 时等待，涨停买入/跌停卖出等方向限制由开盘前可得的 `TradingSessionConstraint` 强制执行。
4. 原始成交价配合 `CorporateAction` 处理现金分红、送股和拆并股，避免直接使用包含未来调整因子的前复权序列。
5. 佣金、最低佣金、卖出印花税、滑点、整手、现金和持仓全部使用 `Decimal` 确定性计算。
6. 输出收益率、最大回撤、年化夏普、胜率、盈亏比、成本和权益曲线；无样本指标返回 `null`。
7. 固定 3 只股票、2025-08-07 至 2026-08-07、沪深 300 基准、初始资金和全部成本参数，使用 3×243 根真实股票日线和 243 根真实指数日线离线复现。
8. 夏普 `>0.5` 是如实报告的基线而非强制美化条件；当前固定结果为 `-0.8463`，未达标原因写入正式说明，不使用未来数据或事后调参追指标。
9. 中文终端总验收直接展示单股、组合、基准、成本、证据拒绝、市场约束、公司行为和安全字段，默认不生成报告文件。
10. `simulation_only=true`、`order_created=false`、`real_trading_allowed=false` 始终强制执行。

### 数据与结论边界

- 固定实验的股票和基准行情是真实抓取后保存的离线快照。
- 滚动信号是明确标注的确定性 Agent 回放，并通过 C3 合约适配器进入回测；它不冒充过去现场运行过的四 Agent 结论。
- 当前实时行业、估值和资金流不能倒灌到过去。实际四 Agent 策略表现需要未来持续积累带发布时点的历史快照再评估，这不影响 D1 回测系统接口和防未来数据验收。
- 不连接券商，不创建模拟盘或真实订单；连续模拟运行属于 D4。

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
- `synthetic_market_bars.json` 仅用于基础契约测试；B2 默认演示使用单独保存的腾讯真实历史样本。任何离线样本都只用于复现，不代表当前实时行情或投资结果。

## 8. 阶段二专业分析接口

```python
query = TechnicalAnalysisQuery(
    symbol="sz000001",
    start_date="20260626",
    end_date="20260806",
    mode="offline",
    limit=30,
)
result = build_default_technical_analysis_runtime().run(query)
analysis = result.report["analysis"]
```

- Runtime 通过 B1 的 `FinancialDataTool` 请求 `market.daily`，再转换为至少 30 根 K 线的 `MarketDataSeries`。
- 输出包含收益率、三条均线、MACD、RSI、KDJ、布林带、支撑阻力、趋势和七项可解释评分。
- 所有指标由确定性 `Decimal` 运算得到，并由 CrossValidator 使用原始 K 线完整重算；LLM 不参与计算。
- 自治 Loop 只允许使用一个技术分析工具，Harness 同时检查 JSON Schema、来源字段和重算结果。
- 输出是技术状态摘要和研究证据，不是投资建议或真实交易信号。

基本面分析接口：

```python
query = FundamentalAnalysisQuery(
    symbol="sz000001",
    mode="offline",
    limit=4,
    start_year="2024",
)
result = build_default_fundamental_analysis_runtime().run(query)
analysis = result.report["analysis"]
```

- Runtime 通过 B1 的 `FinancialDataTool` 请求资产负债表、利润表、现金流量表、财务指标、估值和实时价格。
- 输出包含三大报表关键字段、ROE/ROA、净利润增长、PE/PB/PS、规则估值分位、简化股东收益 DCF 和安全边际。
- `CrossValidator` 从报告附带的六类原始 Data Hub 输出重新计算全部基本面结果。
- DCF 和估值分位都返回计算方法与假设，调用方不能把它们误认为无条件的市场结论。

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

D1 已完成并通过固定多股票终端总验收。下一项小功能进入 D2：先建立统一运行观测记录和可直接阅读的调用链面板，再接独立 Evaluator、运行级熔断与最小工具权限。
