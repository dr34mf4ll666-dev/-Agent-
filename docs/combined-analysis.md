# C1 四 Agent 联合分析与结构化辩论

## 第一切片：四 Agent 并行联合分析

本轮先完成 C1 的“Planner 组织四个 Specialist 并行运行”部分：

```text
CombinedAnalysisQuery
        ↓
Planner
        ↓
技术分析 ─┐
基本面   ─┼─ 并行运行各自的 Runtime、Loop 和 Harness
行业     ─┤
大盘宏观 ─┘
        ↓
aggregate 汇总四份结构化报告和证据
```

调用方只需要使用 `CombinedAnalysisRuntime.run(query)`。它内部使用已有四个 Agent 的 Runtime，不复制技术指标、财务计算、行业规则或宏观规则。

## 第二切片：Claim → Evidence → Reasoning 结构化辩论

在联合报告上调用 `StructuredDebateRuntime.run(query)`，默认生成 2 轮辩论，也可以指定 3 轮。每一轮包含：

- Bull：看涨 Claim、证据引用和 Reasoning；
- Bear：风险 Claim、证据引用和 Reasoning；
- 第 2、3 轮通过 `counter_to` 回应上一轮对方 Claim。

每条证据引用都包含 Specialist 名称、报告路径、实际值、`source` 和 `as_of`。`validate_structured_debate` 会重新从联合报告读取路径，检查值是否一致、来源是否属于原报告、时间是否一致，并要求双方各引用至少两个 Specialist。固定摘要样例见 `docs/examples/structured-debate-sz000001.json`。

## 第三切片：Synthesis、质量检查与市场门控

`C1DecisionRuntime.run(query)` 是完整 C1 的统一入口。它依次复用联合分析和结构化辩论，再由确定性代码完成：

- 按技术 25%、基本面 30%、行业 20%、大盘/宏观 25% 计算四 Agent 加权评分；
- 输出门控前综合倾向和经过市场环境修正后的综合倾向；
- 以技术最新收盘价和基本面实时价的均值为参考价，结合正向证据和风险压力生成 Bear 下限、Bull 上限及完整研究区间；
- 输出 0–100 的证据一致性置信度。它表示多份证据是否完整、平衡、相互支持，不表示上涨概率或盈利概率；
- 运行 Consistency Check，检查证券代码、来源、时间、价格口径和辩论证据回放；
- 运行 Bias Detector，要求 Bull/Bear 都有至少两个 Specialist 支持，并检查联合来源多样性；
- 运行 Market Regime Gate：熊市仓位上限不超过 10%，震荡市不超过 20%，风险偏好较低时不超过 15%。真实交易始终关闭。

固定摘要样例见 `docs/examples/c1-decision-sz000001.json`。

## 输入

`CombinedAnalysisQuery.for_symbol()` 可以用一个证券代码构造四路查询：

- 技术分析使用同一证券的日线；
- 基本面使用同一证券的报表、估值和实时价格；
- 行业使用指定行业，例如 `玻璃行业`；
- 大盘/宏观使用指数，例如 `sh000300`，并把目标证券资金流作为资金面代理。

四路必须使用相同的运行模式；技术、基本面和大盘查询必须使用相同的证券代码。默认模式是离线，显式 `--live` 才访问外部数据。

## 当前输出

汇总报告保留四份完整报告、四份原始证据、四份 Loop 轨迹和四路摘要。摘要示例：

- 技术分析：信号标签和信号评分；
- 基本面：综合评分和标签；
- 行业：综合评分、标签和景气度；
- 大盘/宏观：Market Regime、风险偏好、综合评分。

Graph 会显示 `planner → 四路并行 → aggregate`，并保留每个节点的状态、执行顺序、尝试次数和 trace。固定摘要样例见 `docs/examples/combined-analysis-summary-sz000001.json`。

最终结果还会保留每轮 Claim、Evidence、Reasoning、质量检查、目标价研究区间、置信度、仓位门控和 C1 trace；当前 `next_stage` 为 `trader_and_risk_manager`。

## 运行

```powershell
D:\Anaconda\python.exe Scripts\demo_combined_analysis.py
D:\Anaconda\python.exe Scripts\demo_combined_analysis.py --live --symbol sz000001
D:\Anaconda\python.exe Scripts\demo_combined_analysis.py --rounds 3 --base-position-cap 30
```

离线演示会输出：

```text
planner -> technical,fundamental,industry,macro -> aggregate
```

## 当前边界

C1 已完成四 Agent 并行分析、结构化 Bull/Bear 辩论、Synthesis、目标价研究区间、置信度、Consistency Check、Bias Detector 和 Market Regime Gate。每个 Specialist 自己的来源校验和确定性重算仍然生效；联合层不修改底层金融指标。

当前输出仍是研究结论，不是下单指令。目标价上下限是透明规则形成的研究边界，不是模型对未来价格的承诺；置信度只衡量证据一致性。C2 Trader 现在可以消费该结果生成模拟候选，但完整 Risk Manager、人工确认执行、模拟撮合和真实交易接口仍属于后续任务，真实交易默认关闭。

## 验证

```powershell
D:\Anaconda\python.exe -m unittest tests.test_combined_analysis tests.test_structured_debate tests.test_c1_decision tests.test_demo_combined_analysis -v
```
