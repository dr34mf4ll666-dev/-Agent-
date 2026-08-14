# Scripts

存放自动化校验、报告生成、回放和回测辅助脚本。

脚本应能被命令行重复执行，并在失败时返回明确的非零退出状态。

## 客户前台与团队验收后台（推荐入口）

```powershell
D:\Anaconda\python.exe Scripts\run_dashboard.py
```

当前进程没有 DeepSeek Key 时，终端会隐藏提示输入；输入后本次服务启用 DeepSeek，直接回车则使用本地固定格式。Key 不写入文件，关闭服务后失效。自动化运行可添加 `--no-key-prompt`。

该入口只在 `127.0.0.1` 启动本地页面并自动打开浏览器。根路径 `/` 是客户证券分析前台，`/admin` 是 A1–D4 团队验收后台。后台按钮只映射到登记过的脚本，浏览器不能传入任意系统命令。默认使用离线复现；真实模式只读取数据或调用 DeepSeek。交易始终只进入本地模拟撮合。

## P1 异步分析任务演示

```powershell
D:\Anaconda\python.exe Scripts\demo_analysis_jobs.py
```

该脚本真实演示提交任务、17 个节点状态、结果门禁、Checkpoint 恢复能力、总超时配置和最终客户报告。默认离线，不联网；任务存储只写入自动清理的临时目录。

## P2 统一分析快照演示

```powershell
D:\Anaconda\python.exe Scripts\demo_analysis_snapshot.py
```

该脚本在同一固定查询上直观展示主源成功、备用源接管、最近缓存降级、非关键数据部分缺失和关键数据阻断，并证明重复数据请求已去重。默认离线，不联网、不生成文件。

## 产品命令行整体验收

```powershell
D:\Anaconda\python.exe Scripts\demo_product_acceptance.py
```

该入口对应安装后的 `agent-platform verify-all`，统一检查 A–D 核心交付、客户前台、团队后台、智能解释层和真实交易关闭边界。默认离线运行，不生成报告文件。

## 受约束动态辩论固定评测

```powershell
D:\Anaconda\python.exe Scripts\demo_dynamic_debate_evaluation.py
D:\Anaconda\python.exe Scripts\demo_dynamic_debate_evaluation.py --live
```

该入口对应安装后的 `agent-platform debate-eval`。离线模式用可重复的脚本化 Mock 验证证据拒绝、语义重试、统计和安全边界；`--live` 使用真实 DeepSeek 运行同一固定评测集。默认只输出到终端，显式添加 `--output PATH` 才保存逐次原始结果。

## P7 模型治理演示

```powershell
D:\Anaconda\python.exe Scripts\demo_llm_governance.py
```

该脚本离线展示 Prompt/Schema/策略版本、成功结果缓存、调用预算拒绝、治理快照和“模型只能解释、不能修改金融控制”的安全边界。它不联网、不需要 Key，也不会把这次 Mock 演示当成真实模型质量通过。

## D4 最终交付统一验收

```powershell
D:\Anaconda\python.exe Scripts\demo_final_delivery.py
```

该入口对应安装后的 `agent-platform d4-verify`，从环境检查开始实际运行通用 Harness、C3、D1、D2/D3 和 D4 模拟执行，并核对最终文档包。默认离线、不生成临时报告；时间豁免和真实交易关闭会在终端明确展示。

## D2 统一可观测面板演示

```powershell
D:\Anaconda\python.exe Scripts\demo_observability.py
```

该脚本实际运行成功 Harness、预期失败 Harness、两节点 Graph 和 Mock Model Gateway，统一打印调用链、Token、整次耗时、分层指标和失败率。默认离线且不写报告文件；结尾五项中文验收全部通过才返回 0。

## D2 Harness 工程化总验收

```powershell
D:\Anaconda\python.exe Scripts\demo_d2_engineering.py
```

该入口显示固定 Evaluator、运行级熔断告警、9 个 Agent 最小工具权限和有/无 Harness 六项对比指标。它与安装后的 `agent-platform d2-verify` 共用同一 Runtime；默认离线且不写文件。

## D3 Harness 价值独立验收

```powershell
D:\Anaconda\python.exe Scripts\demo_harness_comparison.py
```

该入口对应安装后的 `agent-platform d3-compare`，单独打印六项总指标和 4 个逐用例原始结果，不生成文件。

## Echo Agent 演示

```powershell
python Scripts\demo_echo.py --task "hello agent platform"
```

该脚本通过 `AgentHarness(EchoAgent())` 运行最小闭环，打印输入、输出、Agent 名称和从 preflight 到 postflight 的完整 trace。它不调用模型、工具或网络。

## Graph 演示

```powershell
python Scripts\demo_graph.py
```

该脚本使用离线数据演示条件分支、节点状态、一次预期故障和 Checkpoint 恢复。默认 Checkpoint 写入 `checkpoints/demo_graph.json`，不会提交到 Git。

可选参数：

- `--route approved|rejected`：选择条件分支；
- `--no-failure`：关闭首次模拟故障；
- `--checkpoint PATH`：指定 Checkpoint 文件位置。

## 完整 Graph Engineering 演示

```powershell
python Scripts\demo_graph_engineering.py
```

该脚本加载 `Workflow/examples/parallel_analysis.yaml`，运行两个并行节点，让其中一个节点经历一次可重试故障，然后输出尝试次数、熔断状态、版本 2 Checkpoint 和 Mermaid 运行状态图。可以用 `--workflow`、`--checkpoint` 和 `--mermaid` 指定输入输出路径。

## Guardrail 演示

```powershell
python Scripts\demo_guardrails.py
```

该脚本通过统一配置注册五类 Guardrail，先展示一次正常通过，再展示误导性关键词被输出检查拦截，以及第三次调用被限流器拦截。终端会显示每条规则的输入和输出 trace。演示完全离线运行。

## 技术分析演示

```powershell
python Scripts\demo_technical_analysis.py
```

默认回放 30 根已真实获取的腾讯 A 股日线，通过 Data Hub、技术 Agent 自己的认知 Loop 和 Harness，打印完整指标、七项评分、来源、Loop trace 与 Guardrail trace。它不调用 LLM 或真实交易；只有显式添加 `--live` 才访问真实行情接口。

## 基本面分析演示

```powershell
python Scripts\demo_fundamental_analysis.py
```

该脚本默认回放已真实验证的基本面样本，打印三大报表关键值、ROE/ROA、净利润增长、PE/PB/PS、规则估值分位、简化股东收益 DCF、安全边际、评分、Loop trace 和 Harness trace。只有显式添加 `--live` 才访问真实财务接口；不连接真实交易。

## 行业分析演示

```powershell
python Scripts/demo_industry_analysis.py
```

该脚本默认回放真实验证的行业快照和 LPR，打印行业画像、景气度、竞争格局、产业链、代表股排序、评分和完整 trace。只有显式添加 `--live` 才访问真实接口；产业链是项目分类模板，代表股排序不是完整成分股排名。

## 大盘/宏观分析演示

```powershell
python Scripts/demo_macro_analysis.py
```

该脚本默认回放真实验证的指数、个股资金流、GDP、SHIBOR、LPR 和研报评级，打印指数趋势、资金面代理、情绪、Market Regime、风险偏好、评分和完整 trace。只有显式添加 `--live` 才访问真实接口；资金面是关联股票代理，不代表全市场资金总量。

## C1 四 Agent 并行联合分析演示

```powershell
python Scripts/demo_combined_analysis.py
python Scripts/demo_combined_analysis.py --rounds 3
python Scripts/demo_combined_analysis.py --base-position-cap 30
```

该脚本一次运行完整 C1：Planner 调度技术、基本面、行业和大盘/宏观四个 Agent，在 Graph 的同一并行波次中运行；随后完成 2 或 3 轮 Claim → Evidence → Reasoning 结构化辩论，并输出综合倾向、Bull/Bear 目标价研究边界、完整区间、证据一致性置信度、Consistency Check、Bias Detector 和 Market Regime 仓位门控。`--base-position-cap` 设置门控前仓位上限；无论输入多少，真实交易都保持关闭。

## C2 Trader 模拟候选信号演示

```powershell
python Scripts/demo_trader.py
python Scripts/demo_trader.py --live --symbol sz000001
```

该脚本先运行完整 C1，再单独演示 Trader 如何确定性输出 `buy`、`sell` 或 `hold` 模拟候选，并打印目标价研究区间、证据一致性置信度、市场环境、研究仓位上限、人工确认标记和 Harness trace。它不运行 Risk Manager；完整 C2 请使用下一节的 `demo_c2_trading.py`。`simulation_only=true`、`order_created=false` 和 `real_trading_allowed=false` 均由代码强制保持。

## C2 Trader + Risk Manager 完整演示

```powershell
python Scripts/demo_c2_trading.py
python Scripts/demo_c2_trading.py --confirm
python Scripts/demo_c2_trading.py --live --confirm --symbol sz000001
```

该脚本运行完整 C1、Trader 和确定性 Risk Manager。默认在仓位超过 10% 时停在人工确认；显式 `--confirm` 后才允许进入后续模拟执行。输出包括单笔 2% 风险、行业 30% 上限、总回撤 15%、交易时段、Market Regime、流动性、止损止盈和人工确认检查。`--live` 只表示市场数据来自真实接口，账户权益、仓位、回撤和确认状态仍是命令行提供的模拟场景；系统不会创建订单。

## C3 单股票完整金融 Graph 演示

```powershell
D:\Anaconda\python.exe Scripts\demo_financial_graph.py
D:\Anaconda\python.exe Scripts\demo_financial_graph.py --confirm
D:\Anaconda\python.exe Scripts\demo_financial_graph.py --live --confirm --symbol sz000001
D:\Anaconda\python.exe Scripts\demo_financial_graph.py --confirm --verify-recovery
```

该脚本用一个入口运行完整 C1、Trader、Market Regime 条件路由、Risk Manager 和 Finalize。终端默认显示十段完整标准化报告，包括四 Agent、来源时间、2–3 轮辩论、Synthesis、Trader、十项风控、Graph 审计和安全边界；代码值采用“中文（英文）”。默认不生成文件，只有显式添加 `--output-dir` 时才保存完整报告与 Graph/Harness 审计日志。未指定止损止盈时自动使用 C1 研究区间边界；大盘看空且候选为买入时走阻断分支。`--verify-recovery` 会模拟 Risk Manager 首次失败，并证明恢复时 C1/Trader 不重复运行。系统不会创建订单。

## C3 二十只股票真实批量验收

```powershell
D:\Anaconda\python.exe Scripts\demo_financial_batch.py --live --confirm --attempts 2
```

脚本默认逐只运行 20 只银行股，每只都经过完整 C1、Trader、条件路由和 Risk Manager。它在终端显示进度、交易建议和汇总，并在内存返回 20 份标准化报告、20 条建议和 20 份 Graph/Harness 审计记录；默认不生成文件。离线模式会被明确拒绝，避免用同一份 fixture 冒充 20 只股票。

## D1 多股票总验收

```powershell
D:\Anaconda\python.exe Scripts\demo_backtest_experiment.py
```

脚本读取固定配置和离线真实行情快照，展示 3 只银行股、沪深 300、历史证据门禁、滚动信号、组合绩效、夏普基线、涨跌停方向权限、公司行为和交易安全边界。默认不生成报告文件。`capture_d1_market_pool.py` 只用于显式刷新真实行情 fixture，普通验收不需要联网。

## D1 单股票原理演示

```powershell
D:\Anaconda\python.exe Scripts\demo_backtest.py
```

脚本回放 30 根已真实抓取的平安银行日线，并使用两条明确标记的脚本化目标仓位信号解释回测执行机制。终端展示信号日、下一交易日执行时间、开盘价、含滑点成交价、佣金、卖出印花税、滑点、收益率、最大回撤、夏普、胜率、盈亏比和安全字段。默认不生成文件；完整验收使用上面的多股票入口。

## 腾讯日线数据 Tool 演示

```powershell
python Scripts\demo_market_data.py
```

默认使用 JSON 回放 4 根已经真实验证的腾讯历史日线。只有显式加 `--live` 才会调用 AKShare 腾讯接口；可通过 `--symbol`、`--start-date`、`--end-date`、`--timeout` 和 `--attempts` 控制有界请求。该脚本只读取行情，不连接真实交易。

## 完整金融 Data Hub 与 MCP

```powershell
python Scripts\demo_financial_data_hub.py
python Scripts\run_financial_mcp.py
```

第一个命令默认离线检查全部 19 个 dataset；这些 fixture 均来自最小真实验证。也可以用 `--live --dataset` 限定一次真实只读请求。第二个命令启动官方 MCP Python SDK 的 stdio Server，提供数据集清单和统一金融数据两个工具。真实交易能力没有注册到 MCP。

## 认知 Loop 演示

```powershell
python Scripts\demo_cognitive_loop.py
```

该脚本演示 Plan、Action、Observation 和 Reflection 闭环。第一次 Action 的参数类型不合法，会在工具执行前被 Harness 拒绝；Agent 根据失败 Observation 修正参数，第二次只通过 `ToolRegistry` 调用已注册工具，并在输出校验通过后结束。演示完全离线，不调用真实 LLM。

## 工作记忆演示

```powershell
python Scripts\demo_working_memory.py
```

该脚本把认知摘要写入容量为 5 的工作记忆。Agent 会读取失败 Observation 并修正工具参数；结束后脚本从 `checkpoints/working_memory.json` 恢复快照，并展示 FIFO 淘汰后的最近条目。可以用 `--snapshot PATH` 指定快照位置。

## 完整 Loop Engineering 演示

```powershell
python Scripts\demo_loop_engineering.py
```

该脚本离线运行 Heartbeat、Cron、Hook 和递归目标触发。所有任务都复用 `CognitiveLoopRunner`，读取选中的项目/组织记忆和上下文，并写入各自独立工作目录。运行台账和三层记忆快照默认保存在已忽略的 `.runtime/a3-loop-engineering/`；可以用 `--runtime PATH` 指定位置。

## Model Gateway 演示

```powershell
python Scripts\demo_model_gateway.py
```

默认使用 Mock 适配器，离线展示结构化输出、token、耗时、尝试次数和 trace。只有显式执行 `python Scripts\demo_model_gateway.py --live --provider deepseek` 才会读取本地 `DEEPSEEK_API_KEY` 并发起一次真实请求；也可以将供应商改成 `openai`。测试不会走真实接口。

## 非金融资料研究演示

```powershell
python Scripts\demo_non_financial_research.py
```

该脚本复用 Model Gateway、认知 Loop、受控工具、Graph、Harness、工作记忆和 Checkpoint，完成“本地资料检索→证据整理→结构化摘要”。默认 Mock 离线运行；`--verify-recovery` 演示失败后只重跑综合节点；`--live` 才会读取 `DEEPSEEK_API_KEY` 并产生真实模型调用。详细说明见 `docs/non-financial-research-demo.md`。
