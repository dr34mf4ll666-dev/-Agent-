# 通用 Agent 平台及证券金融分析应用

这是一个用证券分析检验 Agent 工程能力的实践项目。项目先搭建可编排、可验证、可恢复、可审计的平台骨架，再逐步接入金融数据和专业分析流程。LLM 只负责解释或提出候选方案，指标计算、数据校验、仓位和风控由确定性代码完成。

## 项目进度

阶段一已经完成，现有平台包括 Harness、Loop、Graph/DAG、条件分支和 JSON Checkpoint。项目目前进入阶段二，第一步金融行情数据契约也已完成。

当前仍使用离线模拟数据，尚未接入真实行情 API、专业分析 Agent 和真实 LLM。真实交易始终关闭。

## 快速开始

项目要求 Python 3.11 或更高版本。在项目根目录运行全部测试：

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

## 已实现的能力

### Harness：一次调用的可靠性入口

`AgentHarness` 负责输入检查、Agent 调用、输出检查、可插拔 Guardrail 和有序 trace。调用失败时会保留原始异常和已经发生的生命周期事件，便于定位问题。

### Loop：受控的多步运行

`LoopRunner` 让每一步都经过 Harness，并通过外部完成条件决定何时结束。它支持最大步数和有限重试，不允许任务无限循环。

### Graph：节点编排与恢复

`GraphRunner` 按 DAG 依赖顺序执行节点，支持状态合并、条件边、分支跳过和环检测。`JsonCheckpointStore` 会保存共享状态和节点进度，失败恢复时不会重复运行已经完成的节点。

当前 Graph 是单进程顺序执行版本，还没有真正的并行调度、超时控制或外部工作流文件解析。

### Finance：可追溯的行情数据契约

`MarketBar` 统一表示一根 OHLCV K 线，同时保留证券代码、数据来源、获取时间 `timestamp` 和行情对应时间 `as_of`。价格使用 `Decimal`，输入会经过以下检查：

- OHLC 必须是有限正数，并满足最高价和最低价的范围关系；
- 成交量必须是大于等于零的整数；
- `source`、`timestamp` 和 `as_of` 不能为空；
- 时间必须包含时区，且 `as_of` 不能晚于 `timestamp`。

`MarketDataSeries` 只接受同一证券的数据，并要求 `as_of` 严格递增。测试使用的 `synthetic_market_bars.json` 是人工构造的练习数据，不代表真实证券、真实行情或投资结果。

## 模块关系

```text
金融数据契约
      ↓
专业分析节点（下一步）
      ↓
Graph 组织依赖、分支和恢复
      ↓
Loop 管理单个 Agent 的有限多步运行
      ↓
Harness 负责校验、追踪和错误保留
```

当前 Graph 节点仍是通用 Python 函数，尚未自动强制所有节点使用 Harness 或 Loop。后续接入专业 Agent 时，会在明确的节点接口中完成组合。

## 项目结构

```text
.
├── SPEC.md                 # 当前阶段的目标与明确边界
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

下一项任务是实现确定性的 `TechnicalAnalysisAgent`。它会读取已经通过校验的离线行情序列，计算收益率和简单移动平均线，再输出结构化分析结果。这个阶段仍不接 LLM，也不接真实行情 API。

更详细的阶段边界见 `SPEC.md`，数据字段和时间语义见 `docs/finance-data-contract.md`，当前验收状态见 `checklist.json` 和 `progress.txt`。
