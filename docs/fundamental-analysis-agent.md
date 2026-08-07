# B2 基本面分析 Agent

## 目标与边界

基本面分析 Agent 是 B2 四类 Specialist 中第二个完成的 Agent。它把 B1 Data Hub 的三大报表、财务指标、估值和实时价格组织成一份可追溯、可重算的研究报告。它不直接连接交易、不替 LLM 计算财务数值，也不把模型输出包装成确定的投资结论。

## 一次运行经过什么

```text
FundamentalAnalysisQuery
        ↓
认知 Loop 生成 Plan，并选择允许列表中的 fundamental_analysis
        ↓
FinancialDataTool → 六类 Data Hub dataset
        ↓
确定性引擎读取三大报表、指标、估值和价格
        ↓
估值分位 + 现金流代理 + 折现股东收益 DCF + 综合评分
        ↓
Harness：JSON Schema → 六类来源校验 → 完整结果重算
        ↓
独立报告 / Graph 节点状态
```

调用方只依赖 `FundamentalAnalysisRuntime.run(query)`。Graph 使用同一个 Runtime 的 `run_graph_node(state)`，不会复制一套估值计算逻辑。

## 输入与数据

输入 `FundamentalAnalysisQuery` 包含：

- `symbol`：必须包含市场前缀，例如 `sz000001`；
- `mode`：默认 `offline`，显式使用 `live` 才访问外部接口；
- `limit`：每类财务数据读取 1–12 条报告；
- `start_year`：财务指标查询起始年份。

一次基本面工具调用会读取六类数据：资产负债表、利润表、现金流量表、财务指标、估值和实时价格。每条原始记录都保留 `source`、`timestamp` 和 `as_of`；财务报告期的 `as_of` 是报告期末，不等于公告公开时间，历史回测还需要公告发布时间。

## 确定性计算

报告包含以下内容：

- 资产、负债、股东权益和代码重算的负债率；
- 年度收入、年度净利润、净利率和基本每股收益；
- 经营现金流、资本开支和自由现金流代理值；
- ROE、加权 ROE、ROA 和净利润增长；
- PE、PB、PS、当前价格和市值；
- 六项评分：盈利能力、成长性、资产负债表、估值、现金流、DCF。

所有金额和比例先用 `Decimal` 计算，跨 JSON 输出时再统一格式化为字符串。代码不会接受缺字段数据继续生成“完整报告”。

## 估值分位与 DCF 的诚实口径

当前 B1 已有估值接口，但没有历史 PE/PB/PS 时间序列。因此报告中的 `valuation_percentile` 使用透明的规则区间：PE≤20、PB≤3、PS≤10，倍数越低对应分位越高；报告同时写明 `rule_based_not_historical`。它可以作为项目演示的可解释评分，但不能冒充历史市场分位。

平安银行属于银行类股票，工业企业常用的自由现金流 DCF 不能直接照搬。本轮使用“折现股东收益代理模型”：以年度基本每股收益为基数，使用 Data Hub 的净利润增长作为增长假设（限制在 -2% 到 8%），默认折现率 10%、终值增长率 3%、预测 5 年，最后计算每股内在价值和安全边际。报告会返回每年 EPS、现值、假设和方法名 `discounted_earnings_proxy`，避免把简化模型误称为完整通用 DCF。

## Loop 与 Harness

Loop 的 Plan 是“获取报表和指标、计算估值和 DCF、验证报告”，Action 只能选择 `fundamental_analysis`。工具失败时会产生失败 Observation，并在最多两步内安全停止或重试；成功结果必须经过 Harness 才能成为最终 Observation。

基本面 Agent 使用三层防线：

1. `fundamental_output_schema`：检查查询、分析结果、六类原始数据和评分结构；
2. `fundamental_market_sources`：检查六类数据的每条记录都有 `source`、`timestamp`、`as_of`；
3. `fundamental_value_recompute`：从报告附带的六类原始数据重新计算完整报告，篡改评分、估值、DCF 或安全边际都会失败。

## 样例结果

固定样例报告见 `docs/examples/fundamental-analysis-sz000001.json`。在 2026-08-07 的真实只读样本上，离线回放显示：PE 5.04、PB 0.48、规则估值分位 80.7598%，简化股东收益 DCF 内在价值 30.4966，样本价格 11.1700，安全边际 63.3730%，综合评分 60。

这些数字只证明“真实样本 → 确定性计算 → 可重算报告”的链路可以复现，不代表实时行情或投资建议。

## 运行

```powershell
D:\Anaconda\python.exe Scripts\demo_fundamental_analysis.py
```

真实只读请求示例：

```powershell
D:\Anaconda\python.exe Scripts\demo_fundamental_analysis.py --live --symbol sz000001 --limit 4 --start-year 2024
```

真实模式依赖外部财务接口可用性。无论离线还是真实模式，都不会调用 LLM 或真实交易。

## 验证

```powershell
D:\Anaconda\python.exe -m unittest tests.test_fundamental_analysis_runtime tests.test_demo_fundamental_analysis -v
```

测试覆盖财务计算、样例报告、评分篡改拦截、来源缺失、错误输入、Graph 节点和命令行演示。B2 四类 Specialist 已完成；下一步进入交付包三的综合研判和交易风控。
