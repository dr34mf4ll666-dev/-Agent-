# 项目规范（SPEC）

## 1. 项目目标

构建一个可复用的 Agent 平台，并用证券金融分析作为复杂场景验证它的可靠性。平台需要支持：

- 单个 Agent 的多步运行、记忆和验证闭环；
- 多 Agent 的 Graph 编排、并行、条件分支和断点恢复；
- 输出结构化、数据可追溯、行为可约束、过程可审计；
- 在不接入真实下单的前提下完成分析、风控、模拟执行和回测。

## 2. 当前小功能：P5 统一可观测性和可靠性面板（已完成）

P5 新增 `AnalysisObservabilityRuntime` 与可替换的内存/JSON adapter。每次客户分析提交时生成一个 `trace_id`，并写入任务状态、冻结报告和历史归档；HTTP、任务、数据快照、17 个 Graph 节点、Harness 输出复核、模型解释和数据库归档都记录结构化 span。后台从同一批记录确定性统计成功率、P50/P95、数据源失败率、缓存命中率、重试/降级率和 Token。

客户页面在运行时显示节点实际耗时；失败时显示可操作建议、只重试失败步骤和追踪号。团队后台新增单次分析瀑布、慢节点、来源/缓存/降级、模型成本和最近 trace。观测 adapter 只接受有界元数据，拒绝 API Key、Prompt、授权头、完整输入输出和原始行情记录。离线故障注入已证明数据源失败、重试、缓存降级和 Graph 失败能够落入同一 trace。完整说明见 `docs/analysis-observability.md`。

明确不做：P5 不伪造数据源的逐请求耗时，不新增另一套业务执行流程，不接外部 APM，不做 P6 的自选/比较/导出，也不改变确定性金融计算和真实交易关闭边界。

### 已完成基线：P4 双层报告

P4 新增只读 `ReportViewRuntime.project(report_id, view)`。它只接受 `basic` 和 `professional` 两种视图，从 P3 保存的一份冻结报告生成展示投影，不重新取数、不运行 Graph、不创建分析任务，也不调用模型。两种投影共享同一 `report_id`、`snapshot_id`、结论、分数、价格区间、仓位和风控事实，并带有相同的事实指纹。

客户页面首次使用默认普通版，只显示四段通俗过程、综合结论、主要支持、主要风险、研究价格区间、数据时间和免责声明。用户主动切到专业版后，才显示四个 Agent、17 个真实节点、技术指标、来源与证据时间、评分规则、仓位和风控计算。选择会保存在浏览器中，但不会改变报告本身。

专业版 K 线读取冻结快照中的同一批行情，支持日线/周线、20/40/60 根区间、SMA5、SMA20、成交量、十字线以及鼠标/键盘 OHLCV 提示。四个 Agent 的核心指标、来源和证据时间可展开下钻。专业版不暴露 Harness 配置、Checkpoint 文件、原始日志或后台工程入口。完整说明见 `docs/report-views.md`。

明确不做：P4 不做历史对比、收藏和导出（属于 P6），不做统一链路观测面板（属于 P5），不让浏览器拼接业务事实，不为两个视图分别运行 Agent，也不让 LLM 修改冻结报告。

### 并行保留的增强：受约束动态大模型辩论

客户前台允许用户主动请求 DeepSeek 基于四个 Specialist 的真实证据改写 Bull/Bear 论证语言。模型只能选择后端编号化证据，程序负责还原并复核路径、数值、来源、时间、双方覆盖和轮次关系；虚构数字、违规措辞或未知证据会被拒绝，最多两次后降级到固定辩论。Synthesis、价格区间、置信度、仓位与风控结果不得改变。普通分析不自动触发模型，不产生额外 Token。

当前完成 Mock 离线测试、HTTP `analysis_id` 门禁、客户按钮、两轮展示、无 Key 降级和启动时隐藏输入 DeepSeek Key。`DynamicDebateEvaluationRuntime` 还用固定四 Agent 底稿重复运行 2/3 轮模板与动态辩论，输出证据有效率、观点多样性、正反平衡、重试、降级、耗时、Token、稳定性和逐次结果；CLI 与后台 C1+ 卡片共用该接口。Key 只注入本次服务进程。当前仅剩用户使用有效 Key 完成一次真实 DeepSeek 受控评测并留存结果。

### 已完成基线：D4 最终交付

`FinalDeliveryRuntime.from_project().run()` 是最终验收深 module。它通过一个 interface 完成环境与安全配置检查，实际调用通用 Harness、C3 金融 Graph、D1 回测、D2/D3 工程验收和 D4 本地模拟执行，再核对架构图、Graph Schema、Agent 卡片、数据字典、运行手册和三类报告。CLI、仓库演示和测试都经过同一 seam。

原任务书的连续 1–2 周时间等待由用户于 2026-08-11 明确豁免。最终结果必须显示 `waived_not_proven` 和实际真实日期数，不得声称已经完成长周期稳定性证明。真实交易继续硬关闭。

### 已保留的 D2 工程证据

D2 使用三个深 module：`ObservabilityDashboard` 统一调用链和运行指标，`IndustrialHarness` 集中处理运行级熔断、告警和工具权限，`IndependentEvaluator` 使用固定数据集确定性评分。`D2EngineeringRuntime.from_files().run()` 是总验收 interface，CLI、演示和测试都通过同一 seam 调用。

### 正式验收内容

1. Harness、Graph、Model Gateway 的成功/失败结果统一为可观测记录，展示调用链、Token、整次耗时和失败率。
2. Evaluator 独立于被评估 Agent，固定 4 个任务的预期事实、禁用措辞和工具规则，不读取 Agent 自评分。
3. 连续失败阈值默认为 3；达到阈值后状态为 open、产生结构化 critical 告警，并在 operation 执行前暂停后续调用。
4. reset timeout 后只允许一次 half-open 探测；成功关闭并清零，失败重新打开。
5. 9 个当前 Agent 均有显式最小工具白名单；未知 Agent 默认拒绝，五个工具型 Runtime 在注册和 dispatch 时复核权限。
6. 同一固定任务、数据和脚本化模型首轮输出完成有/无 Harness 对比，输出幻觉率、无效调用、成功率、平均耗时、Token 和恢复率。
7. `agent-platform d2-verify` 和 `Scripts/demo_d2_engineering.py` 共用稳定 Runtime；配置和数据集使用严格版本化 JSON 校验。
8. `requirements.lock` 固定核心测试依赖，GitHub Actions 配置安装锁定依赖后运行 CLI 和完整 unittest。
9. 中文终端直接展示每项结果，默认不生成报告文件；真实交易始终关闭。

### 结论边界

- 固定对比实验得到幻觉率 80%→0%、无效工具调用 1→0、成功率 25%→100%、平均耗时 40ms→86.25ms、Token 80→120、恢复成功率 100%。这是脚本化离线 fixture，不冒充 DeepSeek/OpenAI 线上质量。
- 结构化告警由调用方接入外部通知渠道；当前实现不会擅自发送邮件或消息。运行级熔断状态保存在进程内，服务重启后会重置。
- GitHub Actions 文件已本地校验，但远端 CI 是否成功必须在推送后由 GitHub 实际运行确认。
- 旧 Harness/Graph trace 仍只有事件顺序和整次耗时，不伪造单事件时间戳。

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

推荐的人工展示入口是 `python Scripts/run_dashboard.py`。根路径 `/` 是面向客户的证券分析前台，`/admin` 是面向团队的 A–D 验收后台。后台只允许调用登记过的功能；DeepSeek 在两处都只能解释已有结果，不能覆盖确定性数值、自动执行动作或创建订单。

推荐的命令行整体验收入口是 `python Scripts/demo_product_acceptance.py`，安装后等价命令为 `agent-platform verify-all`。它统一检查 A–D 核心交付、客户前台、团队后台、解释层和交易安全边界。

## 11. 安全底线

- `ALLOW_LIVE_TRADING` 默认必须为 `false`。
- 真实交易能力未列入当前交付范围。
- 数值指标、仓位限制和风险判断必须由确定性代码校验。
- 回测不得使用未来数据；报告要显示数据时间、信号时间和执行时间。

## 12. 下一步

当前 A–D 正式任务与 P1–P5 均已完成。下一项产品化主线是 P6 研究工作台与产品闭环：在冻结报告和双层视图之上增加自选、比较、收藏与导出，不能重新取数或调用模型。
