# Scripts

存放自动化校验、报告生成、回放和回测辅助脚本。

脚本应能被命令行重复执行，并在失败时返回明确的非零退出状态。

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

该脚本读取 30 根离线模拟日线，通过 Harness 运行 `TechnicalAnalysisAgent`，并打印结构化指标、趋势规则、数据来源和 Harness trace。它不调用网络、LLM 或真实交易接口。

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
