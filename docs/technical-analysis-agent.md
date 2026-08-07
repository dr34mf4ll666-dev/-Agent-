# B2 技术分析 Agent

## 目标与边界

技术分析 Agent 是 B2 四类 Specialist 中第一个完成的 Agent。它负责把 B1 Data Hub 提供的日线转换成可追溯、可重算的技术分析报告。它描述确定性规则下的技术状态，不预测收益、不直接给出真实交易指令，也不连接下单接口。

## 一次运行经过什么

```text
TechnicalAnalysisQuery
        ↓
认知 Loop 生成 Plan，并选择允许列表中的 technical_market_analysis
        ↓
FinancialDataTool → Data Hub → market.daily
        ↓
MarketDataSeries 校验 OHLCV、顺序、来源和时间
        ↓
TechnicalAnalysisEngine 确定性计算全部指标与评分
        ↓
Harness：JSON Schema → 来源校验 → 原始 K 线完整重算
        ↓
独立报告 / Graph 节点状态
```

调用方只依赖 `TechnicalAnalysisRuntime.run(query)`。取数、转换、计算、Loop 和 Harness 都隐藏在 Runtime 内部；Graph 使用同一个 Runtime 的 `run_graph_node(state)`，不会另走一套计算逻辑。

## 输入与输出

输入 `TechnicalAnalysisQuery` 包含：

- `symbol`：必须包含市场前缀，例如 `sz000001`；
- `start_date`、`end_date`：`YYYYMMDD`；
- `mode`：默认 `offline`，只有显式使用 `live` 才访问外部接口；
- `limit`：30–500 根日线。

输出包含最新收盘价、单日收益率、SMA5/10/20、MACD(12,26,9)、RSI14、KDJ9、20 日布林带、20 日支撑阻力、均线趋势和七项评分。价格和指标以十进制字符串跨 JSON 传递，评分为 -100 到 100 的整数。

当前七项评分分别是趋势、MACD、RSI、KDJ、布林带位置、接近支撑和接近阻力。每个分项都同时返回分数和触发规则，因此总分可以解释，也可以由代码复算。评分标签只是项目规则下的摘要，不等于买入或卖出建议。

## Agent 自己的 Loop

这个 Agent 的 Plan 是“获取日线、计算指标、验证报告”，Action 只能选择 `technical_market_analysis`。成功 Observation 经过 Harness 后，Reflection 才允许结束。工具或数据失败时可以选择修正，但 `max_steps=2`、`max_tool_retries=0`，因此不会无限循环，也不会无界访问外部接口。

## Harness 配置

技术 Agent 使用三层输出防线：

1. `technical_output_schema`：检查查询、行情、指标、评分和来源字段的结构与范围；
2. `technical_market_sources`：要求每根行情都有 `source`、`timestamp` 和 `as_of`；
3. `technical_indicator_recompute`：从报告附带的原始 K 线重新计算完整分析对象，任何指标或评分被改动都会失败。

此外，`MarketDataSeries` 会更早检查证券是否一致、时间是否递增、OHLC 关系是否合法以及时间是否带时区。这样即使缺少来源的数据在进入输出 Guardrail 前就失败，也不会成为成功 Observation。

## 数据与样例

默认 fixture 保存了 2026-06-26 至 2026-08-06 的 30 根 `sz000001` 腾讯历史日线，来源为 `akshare.stock_zh_a_hist_tx`，获取时间为 2026-08-07。它来自 B1 Data Hub 的一次真实成功请求，但离线回放不代表当前实时行情。

固定样例报告见 `docs/examples/technical-analysis-sz000001.json`。该样本得到 10 分、`neutral`：MACD 贡献 +15，KDJ 贡献 -5，其他分项为 0。这个结果只证明计算、追溯和验证链路可以复现。

## 运行

```powershell
D:\Anaconda\python.exe Scripts\demo_technical_analysis.py
```

真实只读请求示例：

```powershell
D:\Anaconda\python.exe Scripts\demo_technical_analysis.py --live --symbol sz000001 --start-date 20260501 --end-date 20260807 --limit 60
```

真实模式依赖 `.[finance]` 可选依赖和外部服务可用性。无论离线还是真实模式，都不会调用 LLM 或真实交易。

## 验证

```powershell
D:\Anaconda\python.exe -m unittest tests.test_technical_analysis_agent tests.test_technical_analysis_runtime tests.test_demo_technical_analysis -v
```

测试覆盖已知指标结果、错误输入、来源缺失、篡改拦截、独立运行、Graph 节点和命令行演示。B2 四类 Specialist 已完成；下一步进入交付包三的综合研判和交易风控。
