# 通用 Agent 平台及证券金融分析应用

这是一个用证券分析检验 Agent 工程能力的实践项目。项目先搭建可编排、可验证、可恢复、可审计的平台骨架，再逐步接入金融数据和专业分析流程。LLM 只负责解释或提出候选方案，指标计算、数据校验、仓位和风控由确定性代码完成。

## 项目进度

项目已经形成 Harness、Loop、Graph/DAG、Checkpoint、金融数据契约和简化技术分析 Agent 等可运行能力，但这不等于任务书中的整个阶段一和阶段二已经完成。当前任务 1.1、1.3 和 1.4 达到正式完成口径；1.2、2.1 和 2.2 仍为部分完成。

任务 1.3 Graph Engineering 已完成 YAML/JSON 定义、边 Schema、并行、重试、超时、熔断、Checkpoint、Mermaid 可视化和 LangGraph 概念映射。任务 1.2 已有认知闭环和有界工作记忆，下一步继续补项目记忆。金融模块仍使用离线模拟数据，尚未接入真实行情 API、其他专业分析 Agent 和真实 LLM。真实交易始终关闭。

完整任务映射、最终成果和验收条件见 [`ROADMAP.md`](ROADMAP.md)。

## 快速开始

项目要求 Python 3.11 或更高版本。先安装项目依赖：

```powershell
python -m pip install -e .
```

然后在项目根目录运行全部测试：

```powershell
python -m unittest discover -s tests -v
```

安装开发依赖后也可以使用 pytest：

```powershell
python -m pytest
```

### 运行 Graph 演示

```powershell
python Scripts\demo_graph.py
```

默认演示会进入 `approved` 分支，让 `recoverable` 节点在第一次执行时产生预期故障，然后读取 Checkpoint 继续运行。终端会显示节点执行顺序、节点状态和最终的共享状态。

如果只想观察另一条条件分支，可以关闭故障模拟：

```powershell
python Scripts\demo_graph.py --route rejected --no-failure
```

演示产生的 `checkpoints/demo_graph.json` 仅用于本地运行，已经通过 `.gitignore` 排除。

### 运行完整 Graph Engineering 演示

```powershell
python Scripts\demo_graph_engineering.py
```

该演示从 `Workflow/examples/parallel_analysis.yaml` 加载工作流，展示边输入输出 Schema、并行波次、节点重试、超时和熔断配置、版本 2 Checkpoint，并把带运行状态的 Mermaid 图写入 `artifacts/a2_graph.mmd`。

### 运行技术分析演示

```powershell
python Scripts\demo_technical_analysis.py
```

该演示会读取 30 根人工构造的日线，通过 Harness 运行 `TechnicalAnalysisAgent`，并输出单日收益率、SMA5、SMA20、趋势标签、触发规则、数据来源和 trace。

### 运行 Guardrail 演示

```powershell
python Scripts\demo_guardrails.py
```

该演示使用统一配置注册 JSON Schema、来源校验、限流、关键词阻断和交叉验证五类规则，并展示正常通过、输出被拦截和输入被限流时的 Harness trace。

### 运行认知 Loop 演示

```powershell
python Scripts\demo_cognitive_loop.py
```

该演示先提交一个类型错误的工具参数，Harness 会在执行工具前拒绝它。Agent 根据失败 Observation 选择 `revise`，修正参数后再次调用允许列表中的工具，并在输出校验通过后完成任务。

### 运行工作记忆演示

```powershell
python Scripts\demo_working_memory.py
```

该演示在认知 Loop 中记录 Plan、Action、Observation 和 Reflection 摘要。工作记忆容量固定为 5；Agent 读取第一次失败的 Observation 后修正参数，运行结束后再从版本化 JSON 快照恢复最近记忆。

## 已实现的能力

### Harness：一次调用的可靠性入口

`AgentHarness` 负责输入检查、Agent 调用、输出检查、可插拔 Guardrail 和有序 trace。调用失败时会保留原始异常和已经发生的生命周期事件，便于定位问题。

任务书要求的五类 Guardrail 已完成。`GuardrailRegistry` 可以从统一配置创建内置规则，也允许注册自定义插件；配置错误与运行时违规使用不同错误类型。每条规则都会在 trace 中记录输入和输出阶段的开始、通过或失败。具体接口和当前限制见 `docs/harness-guardrails.md`。

### Loop：受控的多步运行

`LoopRunner` 让每一步都经过 Harness，并通过外部完成条件决定何时结束。它支持最大步数和有限重试，不允许任务无限循环。

`CognitiveLoopRunner` 在此基础上增加 `Plan`、`Action`、`Observation` 和 `Reflection`。`ToolRegistry` 是工具允许列表：未注册工具会被拒绝；每次 Action 的参数在执行前由 Harness 检查，工具结果在成为成功 Observation 前再经过输出检查。工具失败会成为可反思的失败 Observation，Agent 可以继续、修正或完成。

`WorkingMemory` 保存当前任务最近的认知摘要，使用固定容量和 FIFO 淘汰，不会随循环无限增长。Agent 在选择 Action 和 Reflection 时读取不可变视图；内存存储与 JSON 存储通过同一快照接口装配。

当前版本仍不是任务书要求的完整 Agent Loop：项目记忆、组织记忆、三类调度、任务隔离、上下文注入和真实 Model Gateway 尚未实现。接口、执行顺序和当前边界见 `docs/cognitive-loop.md` 与 `docs/working-memory.md`。

### Graph：节点编排与恢复

`GraphWorkflowLoader` 可以从版本化 YAML/JSON 加载工作流，节点处理函数只能来自 `NodeRegistry` 允许列表。每条边必须声明 source 输出 Schema 和 target 输入 Schema，状态传递不符合约定时会在下游节点执行前拒绝。

`GraphRunner` 支持顺序或并行拓扑波次、确定性状态合并、条件分支、跳过传播、节点重试、软超时、持久化熔断和 Checkpoint 恢复。并行节点写入同一状态键时会拒绝合并，不依赖线程完成先后静默覆盖。`GraphVisualizer` 可以输出静态或按运行状态着色的 Mermaid。完整配置、错误语义和当前超时边界见 `docs/graph-engineering.md`，与 LangGraph 的关系见 `docs/langgraph-mapping.md`。

### Finance：可追溯的行情数据契约

`MarketBar` 统一表示一根 OHLCV K 线，同时保留证券代码、数据来源、获取时间 `timestamp` 和行情对应时间 `as_of`。价格使用 `Decimal`，输入会经过以下检查：

- OHLC 必须是有限正数，并满足最高价和最低价的范围关系；
- 成交量必须是大于等于零的整数；
- `source`、`timestamp` 和 `as_of` 不能为空；
- 时间必须包含时区，且 `as_of` 不能晚于 `timestamp`。

`MarketDataSeries` 只接受同一证券的数据，并要求 `as_of` 严格递增。测试使用的 `synthetic_market_bars.json` 是人工构造的练习数据，不代表真实证券、真实行情或投资结果。

### TechnicalAnalysisAgent：确定性的技术指标

`TechnicalAnalysisAgent` 从 `AgentRequest.context["market_data"]` 读取经过校验的行情序列，计算最新单日收益率、SMA5 和 SMA20。当前趋势规则为：

- `bullish`：`latest_close > sma_5 > sma_20`；
- `bearish`：`latest_close < sma_5 < sma_20`；
- `mixed`：均线没有形成以上严格关系。

结果通过 `AgentResponse.metadata["analysis"]` 返回，价格和指标转换为 JSON 兼容的十进制字符串。数据不足 20 根或输入类型错误时，Harness 会保留稳定错误和失败 trace。趋势标签只描述当前规则下的技术状态，不构成投资建议。

## 模块关系

```text
金融数据契约
      ↓
专业分析节点（已有技术分析原型）
      ↓
Graph 组织 Schema、并行、分支、可靠性策略和恢复
      ↓
Loop 管理单个 Agent 的计划、受控工具和有限多步运行
      ↓
Harness 负责校验、追踪和错误保留
```

Graph 节点仍是受注册表控制的 Python 函数；后续专业 Agent 可以把自身的 Harness 或 Loop 作为节点处理函数接入，不需要改写 Graph 调度器。

## 项目结构

```text
.
├── SPEC.md                 # 当前阶段的目标与明确边界
├── ROADMAP.md              # 最终成果、正式任务和验收条件
├── AGENTS.md               # 项目协作约定
├── checklist.json          # 功能状态与验收证据
├── progress.txt            # 按日期记录的进度
├── docs/                   # 架构和数据契约说明
├── Rule/                   # Guardrail 和行为规则
├── Skill/                  # 可复用技能说明
├── Workflow/               # Graph/DAG 工作流说明
├── Scripts/                # 可直接运行的演示和辅助脚本
├── MCP/                    # 外部数据与工具适配层
├── SubAgents/              # 后续专业 Agent 定义
├── src/agent_platform/
│   ├── core/               # Harness、Loop、Graph 和 Checkpoint
│   └── finance/            # 金融数据契约与后续分析逻辑
└── tests/                  # 自动化测试和离线 fixture
```

## 开发约束

每个小功能都要留下可运行代码、自动化测试、文档说明和进度证据。外部数据必须保留 `source`、`timestamp` 和 `as_of`；回测必须区分信号时间与执行时间，禁止使用未来数据。

真实交易默认关闭，仓库中不保存 API 密钥、真实账户信息或本地 `.env`。当前阶段只允许模拟撮合和离线验证。

## 下一步

下一项任务是继续完成任务 1.2：增加跨一次 Loop 运行、按项目隔离和持久化的项目记忆；组织记忆及其生命周期在后续小步完成。金融端到端 Graph 要等真实数据和四类专业 Agent 就绪后再组装，但平台 A2 Graph Engineering 已正式完成。

最终交付路线见 `ROADMAP.md`，当前小步边界见 `SPEC.md`，数据字段和时间语义见 `docs/finance-data-contract.md`，正式任务状态见 `checklist.json`，历史工作记录见 `progress.txt`。
