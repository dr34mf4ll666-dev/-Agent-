# Scripts

存放自动化校验、报告生成、回放和回测辅助脚本。

脚本应能被命令行重复执行，并在失败时返回明确的非零退出状态。

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

`powershell
python Scripts/demo_industry_analysis.py
`

该脚本默认回放真实验证的行业快照和 LPR，打印行业画像、景气度、竞争格局、产业链、代表股排序、评分和完整 trace。只有显式添加 `--live` 才访问真实接口；产业链是项目分类模板，代表股排序不是完整成分股排名。

## 大盘/宏观分析演示

`powershell
python Scripts/demo_macro_analysis.py
`

该脚本默认回放真实验证的指数、个股资金流、GDP、SHIBOR、LPR 和研报评级，打印指数趋势、资金面代理、情绪、Market Regime、风险偏好、评分和完整 trace。只有显式添加 `--live` 才访问真实接口；资金面是关联股票代理，不代表全市场资金总量。

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
