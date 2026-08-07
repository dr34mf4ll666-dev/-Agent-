# B2 大盘/宏观分析 Agent

## 目标与边界

大盘/宏观分析 Agent 把指数、关联股票资金流、GDP、SHIBOR、LPR 和研报评级组织成一份市场环境报告，输出指数趋势、资金面代理、情绪、Market Regime 和风险偏好。它输出的是透明规则下的研究状态，不是官方市场分类，也不直接生成买卖指令。

## 一次运行经过什么

`text
MacroAnalysisQuery
        ↓
认知 Loop 生成 Plan，并选择允许列表中的 macro_analysis
        ↓
FinancialDataTool → 六类 Data Hub dataset
        ↓
确定性引擎计算指数趋势、资金面、情绪、Market Regime 和风险偏好
        ↓
Harness：JSON Schema → 六类来源校验 → 完整结果重算
        ↓
独立报告 / Graph 节点状态
`

调用方只依赖 `MacroAnalysisRuntime.run(query)`；Graph 使用同一个 Runtime 的 `run_graph_node(state)`。

## 输入与数据

`MacroAnalysisQuery` 包含指数代码、关联股票代码、离线/真实模式、数据条数和日期范围。一次工具调用读取：

- `macro.index`：例如沪深 300 的多日收盘价和成交量；
- `market.fund_flow`：关联股票的流入、流出、净流入和成交额；
- `macro.gdp`：GDP 当前值和前值；
- `macro.shibor`：1W SHIBOR 当前值和上一期值；
- `macro.policy_lpr`：1Y/5Y LPR；
- `sentiment.research`：研报评级。

每条记录都保留 `source`、`timestamp` 和 `as_of`。资金流当前使用 Data Hub 的个股资金流接口，因此报告明确标为“资金面代理”，不把它说成全市场资金总量。

## 确定性计算

- 指数趋势：计算最近单日收益和窗口收益；窗口收益大于等于 1% 为 `bullish`，小于等于 -1% 为 `bearish`，其余为 `flat`；
- 资金面：计算目标股票净流入和净流入/成交额，输出 `inflow`、`outflow` 或 `balanced`；
- 情绪：统计研报评级，并与指数最近单日表现/关联股票净流入组成透明的市场代理；
- 宏观环境：比较 GDP、1W SHIBOR 和 LPR 的变化；
- Market Regime：指数窗口趋势与资金流同时偏强为 `risk_on`，同时偏弱为 `risk_off`，否则为 `mixed`；
- 风险偏好：由四项评分映射为 `high`、`moderate`、`cautious` 或 `low`。

所有比例和评分由 `Decimal` 确定性计算，LLM 不负责补数或改写结论。

## Loop 与 Harness

Loop 的 Action 只允许调用 `macro_analysis`。Harness 包含：

1. `macro_output_schema`：检查查询、分析结果和六类原始数据结构；
2. `macro_market_sources`：检查六类数据的每条记录都有来源与时间字段；
3. `macro_value_recompute`：从附带原始数据重新计算完整报告，篡改 Market Regime、评分或风险偏好会失败。

## 直观样例

固定样例见 `docs/examples/macro-analysis-sh000300.json`。在 2026-08-07 的真实只读样本上，沪深 300 五日窗口收益约 1.3756%，但平安银行资金净流出约 2.12 亿元，研报评级为中性，规则输出 `Market Regime=mixed`、风险偏好 `low`、综合评分 -15（`neutral`）。

这个结果体现了为什么不能只看指数：指数窗口上涨并不自动等于资金和情绪都支持风险偏好上升。

## 运行与验证

`powershell
D:\Anaconda\python.exe Scripts\demo_macro_analysis.py
D:\Anaconda\python.exe Scripts\demo_macro_analysis.py --live --index-symbol sh000300 --symbol sz000001 --limit 5
D:\Anaconda\python.exe -m unittest tests.test_macro_analysis_runtime tests.test_demo_macro_analysis -v
`

真实模式必须显式使用 `--live`，不会调用 LLM 或真实交易。
