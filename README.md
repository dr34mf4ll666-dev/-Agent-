# 通用 Agent 平台及证券金融分析应用

这是一个用证券分析检验 Agent 工程能力的实践项目。项目先搭建可编排、可验证、可恢复、可审计的平台骨架，再逐步接入金融数据和专业分析流程。LLM 只负责解释或提出候选方案，指标计算、数据校验、仓位和风控由确定性代码完成。

## 项目进度

项目的调整后交付范围已经完成，并形成客户前台与团队验收后台两个 Web 界面。平台包含 Harness、完整 Loop、Graph/DAG、Checkpoint、Model Gateway 和非金融资料研究；金融应用包含 19 类真实数据、四类专业 Agent、C1–C3 综合决策链、回测和本地模拟撮合；工程部分包含可观测性、Evaluator、熔断、最小权限、Harness 对比实验、统一最终验收和完整文档。原“连续运行 1–2 周”时间等待由用户明确豁免，当前只保留单日真实运行证据，因此不宣称已经证明长周期稳定性。真实交易始终关闭。

任务 1.2 Loop Engineering 已完成认知闭环、受控工具、三层记忆、三类触发循环、任务隔离和上下文注入。任务 1.3 Graph Engineering 已完成 YAML/JSON 定义、边 Schema、并行、可靠性、Checkpoint 和可视化。A4 Model Gateway 已通过 DeepSeek 真实调用验证。A5 又在不修改核心框架语义的前提下接入非金融资料研究。B1 已形成统一金融 Data Hub 与只读 MCP Server；行情、财务、宏观行业、新闻、公告、LPR、研报和 Tushare 第二日线来源均已真实验证，19 个 dataset 都有真实最小样本和离线回放。真实交易始终关闭。

完整任务映射、最终成果和验收条件见 [`ROADMAP.md`](ROADMAP.md)。

### 推荐入口：打开分析应用

```powershell
D:\Anaconda\python.exe Scripts\run_dashboard.py
```

如果当前进程还没有 `DEEPSEEK_API_KEY`，启动命令会在终端隐藏提示输入 Key。输入后，客户智能解读、动态多空辩论和后台助手都会使用 DeepSeek；直接回车则使用本地安全解释与固定辩论。Key 只保存在这一次服务进程中，关闭服务后失效，不会写入项目文件。自动化启动时可加 `--no-key-prompt` 跳过询问。

运行后浏览器会打开 `http://127.0.0.1:8765/`，这里是面向客户的证券分析应用。股票池提供 20 只已经通过真实批量链路验证的沪深银行股；选择股票和数据版本后，页面直接展示 K 线、综合观点、四维“研究天平”、技术/基本面/行业/宏观解释、支持与风险因素、研究价格区间及智能解读，不要求客户理解 Graph、Harness 或命令行。平安银行同时支持可复现快照和最新数据，其余 19 只股票只使用最新只读数据，页面会自动切换并禁用不适用的快照选项。

团队演示和工程验收页面保留在 `http://127.0.0.1:8765/admin`。它把 A 平台底座、B 金融数据与四类 Agent、C 联合决策与风控、D 回测与工程验收放在同一条执行轨道中，提供 18 个可操作入口、结果摘要和完整 trace。

如果启动时输入 Key，或当前 PowerShell 已能读取 `DEEPSEEK_API_KEY`，客户前台会使用 DeepSeek 把确定性结果解释成通俗中文，后台助手也会基于当前结果推荐下一项功能；没有 Key 时两处都会使用本地安全解释。模型不能修改指标、仓位和风控，不能自动执行动作，也不能创建真实订单。客户前台说明见 [`docs/client-app.md`](docs/client-app.md)，团队后台说明见 [`docs/control-desk.md`](docs/control-desk.md)。

客户前台还提供手动“生成动态多空解读”：DeepSeek 只能从四个 Agent 的编号化证据目录选择引用并改写论证语言，后端会重新核对证据路径、数值、来源、时间、双方覆盖和违规表达；不合规或未配置 Key 时自动退回固定辩论。该功能不会修改综合分数、价格区间、仓位或风控。

安装项目后也可以运行：

```powershell
agent-platform dashboard
```

### 一条命令验收整个产品

```powershell
D:\Anaconda\python.exe Scripts\demo_product_acceptance.py
```

安装项目后也可以运行 `agent-platform verify-all`。该入口先复现 A–D 核心交付，再检查客户前台、团队后台、DeepSeek/本地解释层和交易安全边界，最终输出一页中文结论。默认不联网、不生成临时报告文件。原 D4 专项入口 `Scripts/demo_final_delivery.py` 和 `agent-platform d4-verify` 继续保留。完整交付说明见 [`docs/final-delivery.md`](docs/final-delivery.md)。

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

### 运行 Echo Agent 演示

```powershell
python Scripts\demo_echo.py --task "hello agent platform"
```

这是最小的 Agent/Harness 健康检查：不调用模型、工具或网络，只把任务原样返回，并打印 preflight、Agent 执行和 postflight trace。

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

默认离线回放 30 根已真实获取的腾讯 A 股日线，不访问网络。技术 Agent 通过 Data Hub、认知 Loop 和 Harness 输出 SMA5/10/20、MACD、RSI、KDJ、布林带、支撑阻力、七项评分、数据来源和完整 trace。显式加 `--live` 才请求真实接口；详细边界与样例见 `docs/technical-analysis-agent.md`。

### 运行基本面分析演示

```powershell
python Scripts/demo_fundamental_analysis.py
```

默认离线回放已真实验证的平安银行财务样本，通过 Data Hub、基本面 Agent 自己的认知 Loop 和 Harness，输出三大报表关键字段、ROE/ROA、净利润增长、PE/PB/PS、规则估值分位、简化股东收益 DCF、安全边际、评分和完整 trace。显式加 `--live` 才请求真实财务数据；估值分位和 DCF 的适用边界见 `docs/fundamental-analysis-agent.md`。

### 运行行业分析演示

```powershell
python Scripts/demo_industry_analysis.py
```

默认回放真实验证的行业快照和 LPR，输出行业画像、景气度、竞争格局、产业链、代表股排序、评分和完整 trace。显式加 `--live` 才请求真实行业和政策数据；详细边界见 `docs/industry-analysis-agent.md`。

### 运行大盘/宏观分析演示

```powershell
python Scripts/demo_macro_analysis.py
```

默认回放真实验证的指数、个股资金流、GDP、SHIBOR、LPR 和研报评级，输出指数趋势、资金面代理、情绪、Market Regime、风险偏好、评分和完整 trace。显式加 `--live` 才请求真实数据；详细边界见 `docs/macro-analysis-agent.md`。

### 运行四 Agent 并行联合分析演示

```powershell
python Scripts/demo_combined_analysis.py
```

默认离线运行 Planner、技术、基本面、行业和大盘/宏观四路 Agent：四个 Specialist 在同一 Graph 并行波次执行，汇总后进行 2 轮 Claim → Evidence → Reasoning 结构化 Bull/Bear 辩论，再输出综合倾向、Bull/Bear 目标价研究边界、完整区间、证据一致性置信度、Consistency Check、Bias Detector 和 Market Regime 仓位门控。结果是研究结论而非下单指令；显式加 `--live` 才请求真实金融数据，详细边界见 `docs/combined-analysis.md`。

### 运行 C3 单股票完整金融 Graph

```powershell
D:\Anaconda\python.exe Scripts\demo_financial_graph.py --confirm
```

该入口只运行一次完整链路：C1 内部完成 Planner、四个 Specialist、辩论和 Synthesis，外层 Graph 再执行 Trader、Market Regime 条件路由、Risk Manager 和最终报告。终端默认显示十段中文（英文代码值）标准化报告，但不生成文件；只有显式添加 `--output-dir` 才保存完整报告和 Graph/Harness 审计日志。大盘看空且 Trader 提出买入时，条件边直接转入阻断分支；未手填止损止盈时使用 C1 研究区间下沿和上沿作为模拟默认值。添加 `--verify-recovery` 可验证临时 Checkpoint 恢复且不重复执行 C1/Trader。显式加 `--live --symbol sz000001` 才请求真实市场数据，账户和确认信息仍是模拟输入。

### 运行 C3 二十只股票真实批量验收

```powershell
D:\Anaconda\python.exe Scripts\demo_financial_batch.py --live --confirm --attempts 2
```

该入口默认逐只分析 20 只银行股，使用真实股票数据和“金融行业”板块数据，终端显示进度与交易建议，并在返回值中保留完整报告和 Graph/Harness 审计记录。默认不生成文件。2026-08-10 的验收结果为请求 20、完成 20、失败 0；所有建议仍是模拟研究结果，绝不创建订单。

### 运行 D1 多股票总验收

```powershell
D:\Anaconda\python.exe Scripts\demo_backtest_experiment.py
```

该入口使用固定的 3 只银行股和沪深 300：每个标的均为 243 根已真实抓取日线。终端直接展示历史证据拒绝、30 个滚动信号、11 次模拟成交、单股/组合/基准结果、成本、夏普基线、涨跌停方向拦截、分红送转和交易安全边界。固定结果为组合收益 `-0.5408%`、夏普 `-0.8463`，未达到 `>0.5` 基线，系统不会用未来数据或事后调参美化结果。回放信号明确标为固定滚动 Agent 规则，不冒充历史现场四 Agent 结论。

### 运行 D1 单股票原理演示

```powershell
D:\Anaconda\python.exe Scripts\demo_backtest.py
```

该演示回放 30 根已真实抓取的平安银行日线，用两条明确标记的脚本化目标仓位信号解释单笔撮合。信号在 T 日收盘后产生，只能在下一根可交易 K 线开盘执行；结果显示佣金、卖出印花税、滑点、收益率、最大回撤、夏普、胜率和盈亏比。脚本默认不生成文件，完整 D1 验收请运行上面的多股票入口。

### 运行 D2 统一可观测面板

```powershell
D:\Anaconda\python.exe Scripts\demo_observability.py
```

该离线演示统一展示 Harness、Graph 和 Model Gateway 的调用链、整次耗时、Token 与失败率，并保留一次预期失败的完整原因。默认只输出中文终端面板，不生成报告文件。详细契约见 `docs/observability.md`。

### 运行 D2 Harness 工程化总验收

```powershell
D:\Anaconda\python.exe Scripts\demo_d2_engineering.py
```

安装项目后也可以运行 `agent-platform d2-verify`。终端会直接展示独立 Evaluator、连续失败熔断与告警、9 个 Agent 工具白名单，以及有/无 Harness 的幻觉率、无效工具调用、成功率、耗时、Token 和恢复率对比。固定实验是离线工程 fixture，不代表真实模型线上质量；详细方法和边界见 `docs/d2-harness-engineering.md`。

### 单独运行 D3 Harness 对比实验

```powershell
D:\Anaconda\python.exe Scripts\demo_harness_comparison.py
```

安装项目后也可以运行 `agent-platform d3-compare`。该界面只展示 D3 六项总指标、4 个逐用例原始结果、恢复状态和成本变化，方便独立验收；数据仍是固定离线工程 fixture。

### 运行 D4 本地持续模拟交易

先用离线数据直观看完整的 C3 决策、本地模拟成交和复盘摘要，不会留下文件：

```powershell
D:\Anaconda\python.exe Scripts\demo_paper_trading.py --confirm
```

去掉 `--confirm` 可以验收人工确认门禁：系统会记录“缺少确认”，但不会模拟成交。只有明确指定 `--ledger` 才会保留一份可恢复账本；后续同一 `session-id` 的运行继续追加到该文件：

```powershell
D:\Anaconda\python.exe Scripts\demo_paper_trading.py --live --confirm --session-id d4-live --ledger .runtime\paper_trading\d4-live.json
```

只查看累计状态，不重新运行四 Agent 或访问网络：

```powershell
D:\Anaconda\python.exe Scripts\demo_paper_trading.py --review-only --ledger .runtime\paper_trading\d4-live.json
```

真实模式会继续复用 C3 的四 Agent、Trader 和 Risk Manager，并另外调用腾讯 `market.realtime` 取得带 `source`、`timestamp`、`as_of` 的模拟执行报价，避免把尚未收盘的日线当成盘中成交价。原时间等待已按用户要求豁免；账本仍会如实统计真实日期，不能把单日记录显示成长周期证明。账本结构、费用规则和安全边界见 `docs/paper-trading.md`。

### 运行腾讯日线数据 Tool

```powershell
python Scripts\demo_market_data.py
```

默认回放已经验证过的 4 根腾讯真实历史日线，不访问网络。显式加 `--live` 才会调用 `akshare.stock_zh_a_hist_tx`；实时模式需要先安装 `.[finance]` 可选依赖。字段差异、成交量转换和当前限制见 `docs/tencent-daily-market-data.md`。

### 运行完整金融 Data Hub

```powershell
python Scripts\demo_financial_data_hub.py
```

默认离线遍历 19 个金融 dataset。显式指定 `--live --dataset ...` 才会访问真实来源；相同真实请求会优先命中本地缓存。MCP stdio 入口为 `python Scripts\run_financial_mcp.py`，完整数据集、可靠性和 Tushare token 边界见 `docs/financial-data-hub.md`。

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

### 运行完整 Loop Engineering 演示

```powershell
python Scripts\demo_loop_engineering.py
```

该演示把 Heartbeat、Cron、Hook 和递归目标触发接入同一个认知 Loop，展示工作/项目/组织三层记忆、Skill 和项目约定注入、六个独立任务目录，以及可恢复的运行台账。

### 运行 Model Gateway 演示

```powershell
python Scripts\demo_model_gateway.py
```

默认使用确定性 Mock，不访问网络、不需要密钥，也不会产生 API 费用。终端会显示模型名称、token、耗时、尝试次数、结构化输出和调用 trace。真实调用必须显式加 `--live`；当前默认真实供应商是 DeepSeek，也可以用 `--provider openai` 切换。具体步骤见 `docs/model-gateway.md`。

### 运行非金融资料研究演示

```powershell
python Scripts\demo_non_financial_research.py
```

默认使用四次脚本化 Mock 调用完成 Plan、Action、Reflection 和证据综合，并输出 Graph 顺序、允许工具、工作记忆、证据、token 与 trace。加 `--verify-recovery` 可以先模拟综合节点失败，再证明恢复时不会重复执行检索和证据整理。可选的 `--live` 会读取当前终端中的 `DEEPSEEK_API_KEY`，具体接入步骤与边界见 `docs/non-financial-research-demo.md`。

## 已实现的能力

### Harness：一次调用的可靠性入口

`AgentHarness` 负责输入检查、Agent 调用、输出检查、可插拔 Guardrail 和有序 trace。调用失败时会保留原始异常和已经发生的生命周期事件，便于定位问题。

任务书要求的五类 Guardrail 已完成。`GuardrailRegistry` 可以从统一配置创建内置规则，也允许注册自定义插件；配置错误与运行时违规使用不同错误类型。每条规则都会在 trace 中记录输入和输出阶段的开始、通过或失败。具体接口和当前限制见 `docs/harness-guardrails.md`。

### Loop：受控的多步运行

`LoopRunner` 让每一步都经过 Harness，并通过外部完成条件决定何时结束。它支持最大步数和有限重试，不允许任务无限循环。

`CognitiveLoopRunner` 在此基础上增加 `Plan`、`Action`、`Observation` 和 `Reflection`。`ToolRegistry` 是工具允许列表：未注册工具会被拒绝；每次 Action 的参数在执行前由 Harness 检查，工具结果在成为成功 Observation 前再经过输出检查。工具失败会成为可反思的失败 Observation，Agent 可以继续、修正或完成。

`WorkingMemory` 保存当前任务最近的认知摘要，使用固定容量和 FIFO 淘汰，不会随循环无限增长。Agent 在选择 Action 和 Reflection 时读取不可变视图；内存存储与 JSON 存储通过同一快照接口装配。

`LongTermMemory` 通过严格命名空间保存项目和组织记忆，只允许显式写入、筛选查询和受控删除。`ContextInjector` 把选中的长期记忆、Skill、项目约定和任务上下文以只读形式交给 Planner、Action 和 Reflection。`TaskWorkspaceManager` 为每次调度运行提供独立目录。

`HeartbeatLoop`、`CronLoop`、`HookLoop` 和 `GoalLoop` 共用持久运行台账，分别按时间槽、Cron 分钟、事件 ID 和目标路径去重。完整接口和边界见 `docs/loop-engineering.md`。

### Graph：节点编排与恢复

`GraphWorkflowLoader` 可以从版本化 YAML/JSON 加载工作流，节点处理函数只能来自 `NodeRegistry` 允许列表。每条边必须声明 source 输出 Schema 和 target 输入 Schema，状态传递不符合约定时会在下游节点执行前拒绝。

`GraphRunner` 支持顺序或并行拓扑波次、确定性状态合并、条件分支、跳过传播、节点重试、软超时、持久化熔断和 Checkpoint 恢复。并行节点写入同一状态键时会拒绝合并，不依赖线程完成先后静默覆盖。`GraphVisualizer` 可以输出静态或按运行状态着色的 Mermaid。完整配置、错误语义和当前超时边界见 `docs/graph-engineering.md`，与 LangGraph 的关系见 `docs/langgraph-mapping.md`。

### Model Gateway：统一模型调用边界

`ModelGateway` 让上层只依赖一个 `generate()` 入口。`MockModelAdapter`、`OpenAIResponsesAdapter` 和 `DeepSeekChatAdapter` 共用同一请求、响应、用量、错误和 trace 契约；Gateway 统一执行结构化结果复核、单次调用超时、有限指数退避和可重试判断。

OpenAI 适配器调用 Responses API；DeepSeek 适配器调用 Chat Completions，并将 JSON Output 交给 Gateway 做本地 Schema 复核。两者都会把响应 ID、实际模型名和 token 用量转换为平台字段。密钥分别只从 `OPENAI_API_KEY` 和 `DEEPSEEK_API_KEY` 读取，默认离线演示和单元测试不会访问真实接口。A4 已通过一次受控 DeepSeek 真实调用验收，完整边界和证据见 `docs/model-gateway.md`。

### Research：非金融通用性证明

`agent_platform.research` 是平台的第一个非金融参考接入。认知 Loop 通过受控工具检索本地资料，Graph 依次执行检索、证据整理和报告综合，Harness 对工具结果和报告执行 Schema、来源与证据编号检查。默认 Mock 和可选 DeepSeek 使用同一装配方式，Checkpoint 可以在综合节点失败后继续运行。完整运行和最小接入步骤见 `docs/non-financial-research-demo.md`。

### Finance：可追溯的行情数据契约

`MarketBar` 统一表示一根 OHLCV K 线，同时保留证券代码、数据来源、获取时间 `timestamp` 和行情对应时间 `as_of`。价格使用 `Decimal`，输入会经过以下检查：

- OHLC 必须是有限正数，并满足最高价和最低价的范围关系；
- 成交量必须是大于等于零的整数；
- `source`、`timestamp` 和 `as_of` 不能为空；
- 时间必须包含时区，且 `as_of` 不能晚于 `timestamp`。

`MarketDataSeries` 只接受同一证券的数据，并要求 `as_of` 严格递增。测试使用的 `synthetic_market_bars.json` 是人工构造的练习数据，不代表真实证券、真实行情或投资结果。

### TechnicalAnalysisAgent：可重算的技术分析 Specialist

`TechnicalAnalysisRuntime.run(query)` 隐藏了 Data Hub 取数、行情契约转换、指标计算、认知 Loop 和 Harness 校验。调用方只需要提供证券代码、日期范围、离线/真实模式和数据条数；同一个 Runtime 也能通过 `run_graph_node(state)` 接入 Graph。

指标包括 SMA5/10/20、MACD、RSI14、KDJ9、布林带和 20 日支撑阻力。趋势、七项评分和标签全部由 `Decimal` 确定性代码生成；CrossValidator 会从原始 K 线重算整个分析对象，拦截被修改或编造的数值。每根行情继续保留 `source`、`timestamp` 和 `as_of`。这些结果只描述规则下的技术状态，不构成投资建议。

### FundamentalAnalysisAgent：可重算的基本面 Specialist

`FundamentalAnalysisRuntime.run(query)` 一次受控地读取资产负债表、利润表、现金流量表、财务指标、估值和实时价格，再由确定性引擎生成报告。报告同时保留原始六类 Data Hub 输出，Harness 会检查每条记录的 `source`、`timestamp`、`as_of`，并重新计算估值、DCF、安全边际和综合评分。

它当前使用规则区间计算透明的估值分位，不把它包装成历史估值分位；针对银行类股票，DCF 使用折现股东收益代理模型，并把假设和限制写进报告。基本面结果是研究证据，不构成投资建议。

### IndustryAnalysisAgent：可追溯的行业 Specialist

`IndustryAnalysisRuntime.run(query)` 统一读取行业快照和 LPR，确定性计算行业画像、竞争格局、政策信号、景气度、项目产业链模板和代表股排序。报告保留原始数据，Harness 会检查来源并重算评分；产业链和代表股排序的适用边界会显式写入报告。

### MacroAnalysisAgent：可重算的大盘/宏观 Specialist

`MacroAnalysisRuntime.run(query)` 统一读取指数、关联股票资金流、GDP、SHIBOR、LPR 和研报评级，确定性计算指数趋势、资金面代理、情绪、Market Regime、风险偏好和评分。同一个 Runtime 可作为 Graph 节点运行；资金面代理和规则化 Market Regime 不被包装成全市场事实或投资建议。

## 模块关系

```text
Mock / OpenAI / DeepSeek Model Gateway
      ↓
单 Agent Loop 与受控工具
      ↓
金融数据契约与专业分析节点
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
├── dev-map.md              # 九类组件、module seam 和证据导航
├── checklist.json          # 功能状态与验收证据
├── progress.txt            # 按日期记录的进度
├── docs/                   # 架构和数据契约说明
├── Rule/                   # Guardrail 和行为规则
├── Skill/                  # 可复用技能说明与可机读 catalog
├── Workflow/               # Graph/DAG 工作流说明
├── Scripts/                # 可直接运行的演示和辅助脚本
├── MCP/                    # 工具/外部 adapter catalog 与后续金融 MCP
├── SubAgents/              # Agent 卡片、权限和验收证据
├── src/agent_platform/
│   ├── core/               # Harness、Loop、Graph 和 Checkpoint
│   ├── research/           # 非金融资料研究参考接入
│   └── finance/            # 金融数据、分析 Graph、风控与回测逻辑
└── tests/                  # 自动化测试和离线 fixture
```

## 开发约束

每个小功能都要留下可运行代码、自动化测试、文档说明和进度证据。外部数据必须保留 `source`、`timestamp` 和 `as_of`；回测必须区分信号时间与执行时间，禁止使用未来数据。

真实交易默认关闭，仓库中不保存 API 密钥、真实账户信息或本地 `.env`。当前阶段只允许模拟撮合和离线验证。

## 当前结论

A1–A5、B1–B2、C1–C3、D1–D4 的调整后交付范围全部完成。项目现在是一套可运行、可验证的通用 Agent 平台原型，以及建立在其上的证券金融分析参考应用。它适合教学、实训、面试展示和继续产品化，但不保证盈利、不管理真实资金，也没有证明一至两周的长周期稳定性。

最终交付路线见 `ROADMAP.md`，当前小步边界见 `SPEC.md`，数据字段和时间语义见 `docs/finance-data-contract.md`，正式任务状态见 `checklist.json`，历史工作记录见 `progress.txt`。
