# 项目规范（SPEC）

## 1. 项目目标

构建一个可复用的 Agent 平台，并用证券金融分析作为复杂场景验证它的可靠性。平台需要支持：

- 单个 Agent 的多步运行、记忆和验证闭环；
- 多 Agent 的 Graph 编排、并行、条件分支和断点恢复；
- 输出结构化、数据可追溯、行为可约束、过程可审计；
- 在不接入真实下单的前提下完成分析、风控、模拟执行和回测。

## 2. 当前小功能：B2 四类专业分析 Agent 收尾

本轮完成 B2 剩余的行业 Agent 和大盘/宏观 Agent。至此技术、基本面、行业、大盘/宏观四类 Agent 都统一消费 B1 Data Hub，各自拥有确定性分析引擎、自治认知 Loop、Harness、Graph 节点、真实只读验证、离线 fixture、样例报告和自动化测试。B2 正式任务 T2.2 已达到四类 Specialist 的交付包验收条件；后续进入交付包三，不能把这些分析结果直接当成交易指令。

### 本轮验收内容

1. 行业 Agent 通过 `IndustryAnalysisRuntime.run(query)` 独立运行，并通过 `run_graph_node(state)` 接入 Graph。
2. 行业 Agent 确定性计算行业画像、竞争格局、LPR 政策信号、景气度、产业链模板、代表股排序和四项评分。
3. 大盘/宏观 Agent 通过 `MacroAnalysisRuntime.run(query)` 独立运行，并通过 `run_graph_node(state)` 接入 Graph。
4. 大盘/宏观 Agent 确定性计算指数趋势、关联股票资金面代理、研报情绪、GDP/SHIBOR/LPR 环境、Market Regime、风险偏好和评分。
5. 两个 Agent 都拥有 Plan、Action、Observation、Reflection 闭环，只允许调用各自的受控分析工具，最多运行两步。
6. 两个 Agent 都使用 JSON Schema、完整来源校验和代码重算 Guardrail；篡改评分、排序、Regime 或风险偏好会被拒绝。
7. 默认离线演示回放 2026-08-07 真实验证的样本；真实模式必须显式使用 `--live`，外部失败时仍可离线复现。
8. 测试覆盖正常路径、来源缺失、结果篡改、非法查询、Graph 节点和命令行演示。

### 口径边界

- “估值分位”是透明的规则区间分位，不冒充历史估值分位；报告会明确标注方法。
- 银行不适合直接套用工业企业自由现金流 DCF，本轮使用“折现股东收益代理模型”，并在报告中明确这是简化模型。
- LLM 不计算、改写或补全财务数值；指标、估值、DCF 和评分全部由确定性代码计算。
- 结果不转换成买卖指令，不连接真实下单。
- 不修改 Harness、Loop、Graph、Memory 或 Model Gateway 的公共 interface。

### 已完成基线

B1 Data Hub 与只读 MCP 已完成；B2 四类 Agent 均已完成各自的指标/规则、评分、独立 Loop、Harness、Graph 节点、真实样本和离线演示。

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

B2 四类 Agent 已完成，T2.2 已达到交付包二的四类 Specialist 验收条件。下一小功能进入交付包三：用 Graph 编排四类分析，加入综合研判、结构化辩论、Trader 和 Risk Manager。
