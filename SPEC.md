# 项目规范（SPEC）

## 1. 项目目标

构建一个可复用的 Agent 平台，并用证券金融分析作为复杂场景验证它的可靠性。平台需要支持：

- 单个 Agent 的多步运行、记忆和验证闭环；
- 多 Agent 的 Graph 编排、并行、条件分支和断点恢复；
- 输出结构化、数据可追溯、行为可约束、过程可审计；
- 在不接入真实下单的前提下完成分析、风控、模拟执行和回测。

## 2. 当前小功能：C2 Trader 与 Risk Manager 正式完成

本轮用 `C2TradingRuntime` 串联 Trader 和确定性 Risk Manager。Trader 从完整 C1 报告生成 `buy`、`sell` 或 `hold` 模拟候选；Risk Manager 再检查账户风险、仓位、回撤、交易时段、市场环境、流动性、止损止盈和人工确认。完整结果只给出后续模拟执行许可，不创建订单。正式任务 T3.2 达到验收条件。

### 本轮验收内容

1. `TraderRuntime` 只接收完整 C1 结果，按综合倾向、加权评分和置信度确定性输出 `buy`、`sell` 或 `hold`，并继承目标区间和完整来源时间。
2. `RiskContext` 显式提供模拟账户权益、当前/请求仓位、其他同业暴露、组合回撤、成交额、评估时间、止损止盈和人工确认状态。
3. 单笔预计亏损不得超过账户权益 2%；最终单行业暴露不得超过 30%；组合回撤超过 15% 时禁止新增仓位并把已有目标仓位降低 50%。
4. 买入必须位于 A 股交易时段、通过 Market Regime、平均每日成交额不低于 1000 万元、成交额参与率不超过 10%，且止损/参考价/止盈顺序正确、收益风险比不低于 1.5。
5. 最终批准仓位取请求仓位、C1 市场门控、单笔风险、行业和流动性上限中的最小值；超过 10% 必须有显式人工确认。
6. Risk Manager 输出 `approved`、`adjusted`、`pending_human_confirmation`、`blocked`、`forced_reduction` 或 `no_action`，并保留每项检查和计算过程。
7. Risk Manager 在 Harness Pre-Flight 计算全部十项硬规则，执行后再经过 JSON Schema 与确定性结果重算；Trader 和 Risk Manager 均支持独立运行、统一 C2 入口和 Graph 节点。
8. `simulation_only=true`、`order_created=false`、`real_trading_allowed=false` 由代码强制执行；离线和真实市场数据整链均可运行。

### 明确不做

- 不在 C2 实现 C3 的完整金融 Graph 条件边、Checkpoint 恢复或 20 只股票批量运行。
- 不接入真实券商账户；演示中的账户权益、仓位、回撤、成交额和确认状态都是显式模拟输入。
- 不创建模拟订单或真实订单；C2 只决定后续模拟执行是否被允许。
- 不实现撮合、费用、滑点或回测，这些属于后续交付包。
- 不让 LLM 决定交易动作、仓位、风控阈值或覆盖安全字段。

### 已完成基线

B1 Data Hub、B2 四类 Specialist、完整 C1 和 Trader 第一切片作为本轮基线。本轮补齐 Risk Manager 和完整 C2 统一入口，不改变前序金融分析逻辑。

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

C2 已完整完成 Trader、确定性 Risk Manager、人工确认门、模拟执行许可和真实交易关闭。下一小功能进入 C3：完整金融 Graph、条件边、Checkpoint 恢复和不少于 20 只股票的批量运行。
