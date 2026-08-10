# docs

存放项目架构、设计决策、数据契约、运行流程和阶段性复盘文档。

正文文档应说明背景、约束、输入输出和验收方式；临时笔记不要替代 `SPEC.md`、`checklist.json` 或 `progress.txt`。

当前专项文档：

- `finance-data-contract.md`：金融行情字段、时间和来源语义。
- `tencent-daily-market-data.md`：B1 腾讯真实日线 Tool、字段差异、成交量转换、错误与运行方式。
- `financial-data-hub.md`：B1 全类别 Data Hub、缓存限流、硬超时、MCP Server 和 Tushare 最终验收结果。
- `technical-analysis-agent.md`：B2 技术分析 Agent 的指标、Loop、Harness、Graph 接入、真实样本和运行方式。
- `fundamental-analysis-agent.md`：B2 基本面 Agent 的三大报表、估值、DCF、Loop、Harness、Graph 接入和验收方式。
- `industry-analysis-agent.md`：B2 行业 Agent 的行业画像、竞争、政策、景气度、产业链、代表股排序和验收方式。
- `macro-analysis-agent.md`：B2 大盘/宏观 Agent 的指数、资金面代理、情绪、Market Regime、风险偏好和验收方式。
- `combined-analysis.md`：C1 四 Agent 并行编排、结构化辩论、Synthesis、目标区间、置信度、质量检查和 Market Regime 门控。
- `trader.md`：C2 Trader 如何把 C1 结论转换为可校验的模拟候选信号，以及当前 Risk Manager 边界。
- `risk-manager.md`：C2 完整 Trader→Risk Manager 链、固定风控阈值、模拟账户上下文和验收方式。
- `financial-graph.md`：C3 单股票/批量完整 Graph、看空条件路由、Checkpoint 恢复、20 只真实股票验收和自动止损止盈来源。
- `backtest.md`：D1 历史时点证据、C3 信号适配、撮合成本、市场约束、公司行为、固定多股票实验和基线结果。
- `observability.md`：D2 Harness/Graph/Model Gateway 统一观测契约、调用链、Token、耗时、失败率和中文验收面板。
- `d2-harness-engineering.md`：D2 独立 Evaluator、运行级熔断告警、Agent 最小工具权限、稳定 CLI/CI 和 Harness 对比实验。
- `harness-guardrails.md`：五类 Guardrail、配置注册、错误语义和 trace。
- `cognitive-loop.md`、`working-memory.md`、`loop-engineering.md`：认知闭环、三层记忆、调度和上下文注入。
- `graph-engineering.md`、`langgraph-mapping.md`：Graph 配置、可靠性和框架映射。
- `model-gateway.md`：Mock、OpenAI 和 DeepSeek 的统一模型 interface。
- `non-financial-research-demo.md`：A5 跨领域接入、证据约束和恢复验证。

根目录 `dev-map.md` 负责把九类 Harness 组件、核心 module、接入 seam 和验证入口连起来。
