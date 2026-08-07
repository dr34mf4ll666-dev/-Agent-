# B2 行业分析 Agent

## 目标与边界

行业分析 Agent 把 B1 的行业板块快照和 LPR 政策数据组织成一份可追溯的行业研究报告，覆盖行业画像、竞争格局、政策环境、景气度、产业链和代表股排序。它不连接交易，也不把有限的板块快照包装成完整的行业数据库。

## 一次运行经过什么

`text
IndustryAnalysisQuery
        ↓
认知 Loop 生成 Plan，并选择允许列表中的 industry_analysis
        ↓
FinancialDataTool → industry.snapshot + macro.policy_lpr
        ↓
确定性引擎计算行业画像、竞争、景气度、产业链和代表股排序
        ↓
Harness：JSON Schema → 两类来源校验 → 完整结果重算
        ↓
独立报告 / Graph 节点状态
`

调用方只依赖 `IndustryAnalysisRuntime.run(query)`；Graph 使用同一个 Runtime 的 `run_graph_node(state)`。

## 输入与数据

`IndustryAnalysisQuery` 包含行业名称、离线/真实模式、板块样本数和 LPR 查询日期。一次工具调用读取：

- `industry.snapshot`：行业公司数、平均价格、板块涨跌幅、成交量、成交额和数据源提供的代表股票；
- `macro.policy_lpr`：最近几期 1Y/5Y LPR，用于观察政策利率是否变化。

每条原始记录都保留 `source`、`timestamp` 和 `as_of`。行业快照没有独立发布时间，因此 `as_of` 使用数据抓取时刻，这是 Data Hub 已明确的时间语义。

## 确定性计算

- 行业画像：目标行业公司数、平均价格、涨跌幅、成交量/额和代表股票；
- 竞争格局：样本中行业数量、公司数最多的行业；
- 景气度：板块涨跌幅大于等于 2% 为 `hot`，非负且低于 2% 为 `improving`，小于 0 为 `weakening`；
- 政策：比较最新一期与上一期 1Y LPR，输出 `easing`、`stable` 或 `tightening`；
- 产业链：按项目维护的透明分类模板输出上游、中游、下游；
- 龙头排序：按数据源给出的各板块代表股票涨跌幅排序；
- 四项评分：景气度、政策、竞争和代表股表现。

产业链是项目分类模板，不是实时抓取的因果供应链；龙头排序也不是全部成分股排名，报告会把这两个限制写进 `caveats`。

## Loop 与 Harness

Loop 的 Action 只允许调用 `industry_analysis`。Harness 包含：

1. `industry_output_schema`：检查查询、分析结果和原始数据结构；
2. `industry_market_sources`：检查行业和 LPR 的每条记录都有来源与时间字段；
3. `industry_value_recompute`：从附带原始数据重新计算完整行业报告，篡改评分或排序会失败。

## 直观样例

固定样例见 `docs/examples/industry-analysis-glass.json`。离线回放显示：玻璃行业 19 家公司，板块涨跌幅 4.3554%，景气度为 `hot`；最近几期 1Y LPR 未变，政策信号为 `stable`；样本综合评分 40（`positive`）。Loop 和三类 Harness Guardrail 全部通过。

这些数字证明“真实样本 → 确定性计算 → 可重算报告”链路可复现，不代表实时投资结论。

## 运行与验证

`powershell
D:\Anaconda\python.exe Scripts\demo_industry_analysis.py
D:\Anaconda\python.exe Scripts\demo_industry_analysis.py --live --sector 玻璃行业 --limit 5
D:\Anaconda\python.exe -m unittest tests.test_industry_analysis_runtime tests.test_demo_industry_analysis -v
`

真实模式必须显式使用 `--live`，不会调用 LLM 或真实交易。
