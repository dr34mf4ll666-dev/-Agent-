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

## 当前主线

交付包一的技术能力和管理证据完成后，B1 又完成了统一 Data Hub、AKShare/Tushare 多类真实数据、可靠性层、全真实样本离线回放和 MCP Server。当前主线进入 B2 四类专业分析 Agent。

真实交易继续关闭。任何新外部数据都必须保留 `source`、`timestamp` 和 `as_of`，任何密钥都只能来自本地环境变量。
