# 开发地图（dev-map）

这张地图回答三个问题：一个功能应该改哪里、通过哪个稳定接口接入、去哪里核对运行证据。最终验收仍以 `ROADMAP.md` 和 `checklist.json` 为准。

## 九类 Harness 组件

| 类别 | 职责 | 当前实际证据 | 新增内容放置位置 |
| --- | --- | --- | --- |
| SPEC | 限定当前小功能的目标和不做事项 | `SPEC.md` | 只描述当前一个可验收小功能 |
| Rule | 记录并强制执行安全规则 | `Rule/README.md`、`core/guardrails.py`、Guardrail 测试和演示 | 规则说明放 `Rule/`，执行代码放所属 module |
| Skill | 管理可复用动作及其输入输出 | `Skill/catalog.json`、ContextInjector 和 A3/A5 演示 | 新 Skill 先登记 interface、implementation 和 evidence |
| Workflow | 声明 Graph 节点、边和 Schema | `Workflow/examples/*.yaml`、Graph 演示 | 声明放 `Workflow/`，处理函数通过 NodeRegistry 注册 |
| Scripts | 提供可重复运行入口 | `Scripts/demo_*.py` 和对应演示测试 | 每个主要能力至少有一个离线入口 |
| MCP | 管理工具与外部适配器状态 | `MCP/catalog.json`、Tool/Model adapter 测试 | 未经真实验证的外部适配器保持 pending |
| SubAgent | 管理 Agent 角色、权限和验收证据 | `SubAgents/catalog.json` | 每个 Agent 登记 interface、工具权限、模型权限和 Guardrail |
| dev-map | 指引代码与证据位置 | 本文件及 `tests/test_project_bootstrap.py` | 模块或接入方式变化时同步更新 |
| 任务看板 | 记录正式任务状态和证据 | `checklist.json`、`progress.txt`、Git | 只有验收条件全部满足才标记 done |

## 核心 module 与接入 seam

| Module | 调用方需要知道的 interface | 典型 adapter 或实现 | 验证入口 |
| --- | --- | --- | --- |
| Harness | `AgentHarness.run(request)` | Echo、Research Reporter、Technical Agent | `demo_echo.py`、`demo_guardrails.py` |
| Cognitive Loop | `CognitiveLoopRunner.run(request)` | Research Planner、ScheduledContextAgent | `demo_cognitive_loop.py`、`demo_loop_engineering.py` |
| Tool | `name` 与 `run(arguments)` | LocalDocumentSearchTool、DailyMarketDataTool、FinancialDataTool | `demo_non_financial_research.py`、`demo_market_data.py`、`demo_financial_data_hub.py` |
| Graph | YAML/JSON + NodeRegistry + `GraphRunner.run()` | A2 并行工作流、A5 研究工作流 | `demo_graph_engineering.py`、A5 演示 |
| Memory | 工作记忆与长期记忆的统一 store interface | 内存和 JSON adapter | `demo_working_memory.py`、A3 演示 |
| Model Gateway | `ModelGateway.generate(request)` | Mock、OpenAI、DeepSeek adapter | `demo_model_gateway.py` |
| Finance Data | `FinancialDataHub.fetch(dataset, params, mode)` | 子进程真实 provider、JSON fixture、缓存与限流 adapter | `demo_financial_data_hub.py`、`test_financial_data_hub.py` |
| Financial MCP | `list_financial_datasets`、`get_financial_data` | 官方 MCP Python SDK 1.x stdio server | `run_financial_mcp.py`、MCP call_tool 测试 |
| Technical Specialist | `TechnicalAnalysisRuntime.run(query)`、`run_graph_node(state)` | B1 Data Hub、确定性指标引擎、认知 Loop、三层 Harness | `demo_technical_analysis.py`、`test_technical_analysis_runtime.py` |
| Fundamental Specialist | `FundamentalAnalysisRuntime.run(query)`、`run_graph_node(state)` | B1 六类财务 dataset、确定性估值/DCF 引擎、认知 Loop、三层 Harness | `demo_fundamental_analysis.py`、`test_fundamental_analysis_runtime.py` |
| Industry Specialist | `IndustryAnalysisRuntime.run(query)`、`run_graph_node(state)` | B1 行业/LPR dataset、确定性行业画像与评分引擎、认知 Loop、三层 Harness | `demo_industry_analysis.py`、`test_industry_analysis_runtime.py` |
| Macro Specialist | `MacroAnalysisRuntime.run(query)`、`run_graph_node(state)` | B1 指数/资金/宏观/研报 dataset、确定性 Regime 与风险偏好引擎、认知 Loop、三层 Harness | `demo_macro_analysis.py`、`test_macro_analysis_runtime.py` |
| Combined Analysis | `CombinedAnalysisRuntime.run(query)`、`run_graph_node(state)` | Planner、四 Specialist 并行 Graph 波次、报告/证据/来源/Loop 汇总和联合层 Schema 校验 | `demo_combined_analysis.py`、`test_combined_analysis.py`、`docs/combined-analysis.md` |
| Structured Debate | `StructuredDebateRuntime.run(query)`、`run_graph_node(state)` | 2–3 轮 Claim → Evidence → Reasoning、证据路径/来源/as_of 校验和双方证据平衡检查 | `demo_combined_analysis.py`、`test_structured_debate.py`、`docs/combined-analysis.md` |
| C1 Decision | `C1DecisionRuntime.run(query)`、`run_graph_node(state)` | 四路加权 Synthesis、Bull/Bear 研究边界、目标区间、置信度、Consistency/Bias 和 Market Regime 门控 | `demo_combined_analysis.py`、`test_c1_decision.py`、`docs/combined-analysis.md` |
| Trader | `TraderRuntime.run(query)`、`run_graph_node(state)` | C1 输入校验、buy/sell/hold 模拟候选、目标区间/来源传递、Harness 重算和禁止创建订单 | `demo_trader.py`、`test_trader_runtime.py`、`docs/trader.md` |
| Risk Manager | `RiskManagerRuntime.run(query)`、`run_graph_node(state)` | 2% 单笔风险、30% 行业上限、15% 回撤、时段、Regime、流动性、止损止盈和人工确认 | `demo_c2_trading.py`、`test_risk_manager.py`、`docs/risk-manager.md` |
| C2 Trading | `C2TradingRuntime.run(query)`、`run_graph_node(state)` | Trader→Risk Manager 统一入口、模拟执行许可和真实交易硬关闭 | `demo_c2_trading.py`、`test_demo_c2_trading.py`、`docs/risk-manager.md` |
| Financial Graph | `FinancialGraphRuntime.run(query)`、`run_graph_node(state)` | C1→Trader→Market Regime 条件路由→Risk Manager/阻断→Finalize | `demo_financial_graph.py`、`test_financial_graph.py`、`docs/financial-graph.md` |
| Financial Batch | `FinancialBatchRuntime.run(query)` | 隔离运行多只股票并汇总标准化报告、交易建议和 Graph/Harness 审计记录 | `demo_financial_batch.py`、`test_financial_batch.py`、`docs/financial-graph.md` |
| Backtest | `BacktestEngine.run(request)`、`BacktestExperimentRunner.run(...)` | 历史时点 C3 适配、下一开盘撮合、成本、停牌/涨跌停、公司行为、固定多股票组合和基准 | `demo_backtest_experiment.py`、`test_backtest_completion.py`、`docs/backtest.md` |
| Observability | `ObservationAdapter.from_execution(...)`、`ObservabilityDashboard.build(records)` | Harness/Graph/Model 结果归一化、调用链、Token、耗时、失败率与失败原因 | `demo_observability.py`、`test_observability.py`、`docs/observability.md` |
| Industrial Harness | `IndustrialHarness.run(agent, operation, requested_tools)` | 连续失败熔断、暂停、half-open 恢复、结构化告警与工具权限前置检查 | `demo_d2_engineering.py`、`test_industrial_harness.py`、`docs/d2-harness-engineering.md` |
| Independent Evaluator | `IndependentEvaluator.evaluate(dataset, candidates)` | 固定事实/禁用措辞/工具规则评分与六项对比指标 | `test_evaluation.py`、包内 D2 数据集、`docs/d2-harness-engineering.md` |
| D2 Engineering | `D2EngineeringRuntime.from_files().run()`、`agent-platform d2-verify` | 配置校验、总验收、依赖锁定和 CI 入口 | `demo_d2_engineering.py`、`test_d2_engineering.py`、`.github/workflows/ci.yml` |

## 当前主线

交付包一、B1 金融 Data Hub、B2 四个 Specialist、C1–C3 综合决策链、D1 回测系统和 D2 Harness 工程化均已完成。当前进入 D4/T4.3 连续模拟运行与最终交付；真实交易保持关闭。

真实交易继续关闭。任何新外部数据都必须保留 `source`、`timestamp` 和 `as_of`，任何密钥都只能来自本地环境变量。
