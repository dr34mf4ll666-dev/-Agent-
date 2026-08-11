# C3 完整金融 Graph

## 当前完成范围

`FinancialGraphRuntime.run(FinancialGraphQuery)` 是 C3 的单股票统一入口。调用方只提交 C1 查询和模拟账户风控上下文，模块内部通过自研 `GraphRunner` 运行：

```text
C1 Research
  └─ Planner → 四 Specialist 并行 → Aggregate → Debate → Synthesis
        ↓
Trader
        ↓
Market Regime 条件路由
   ├─ 正常候选 → Risk Manager
   └─ 看空买入 → 确定性阻断
        ↓
Finalize 标准化报告
```

C1 仍是一个深 module：四类分析、辩论、质量检查和综合结论都保留在它自己的实现中。C3 只通过已有 interface 组合这些能力，没有复制指标、评分、Trader 或风控公式。

批量入口 `FinancialBatchRuntime.run(FinancialBatchQuery)` 也不复制单股票逻辑。它为每只股票创建隔离的 `FinancialGraphRuntime`，一只失败不会遮蔽其余股票，并汇总三类内存结果：完整报告、交易建议清单和 Graph/Harness 审计日志。

## 条件边

Trader 提出 `buy` 且 Market Regime 为 `bearish`、`bear` 或 `risk_off` 时，Graph 选择 `market_bearish_skip`，不会执行 Risk Manager 节点，而是直接生成 `blocked + hold` 的安全结果。卖出、持有和非看空买入继续进入 Risk Manager。

该分支是确定性代码，不由 LLM 选择。最终报告会重新根据 Trader 动作和 Market Regime 计算应走路线，篡改 `selected_path` 会被拒绝。

## 止损止盈默认值

调用方可以显式提供止损价和止盈价。若未提供，Graph 在 C1 完成后使用研究目标区间下沿和上沿作为模拟默认值，并分别标记：

- `stop_loss_source=c1_target_lower`
- `take_profit_source=c1_target_upper`

这些只是透明、可复算的演示默认值，不是保证成交价或真实投资指令。

## 运行

```powershell
# 默认停在人工确认
D:\Anaconda\python.exe Scripts\demo_financial_graph.py

# 完整通过模拟人工确认
D:\Anaconda\python.exe Scripts\demo_financial_graph.py --confirm

# 真实市场数据 + 模拟账户上下文
D:\Anaconda\python.exe Scripts\demo_financial_graph.py --live --confirm --symbol sz000001

# 故意让 Risk Manager 首次失败，再从临时 Checkpoint 恢复
D:\Anaconda\python.exe Scripts\demo_financial_graph.py --confirm --verify-recovery

# 20 只银行股真实批量验收（默认不生成报告文件）
D:\Anaconda\python.exe Scripts\demo_financial_batch.py --live --confirm --attempts 2
```

离线确认演示当前输出 `c1_research → trader → market_route → risk_manager → finalize`，批准模拟目标仓位 15%，预计单笔亏损 0.98%；`market_bearish_skip` 被明确记录为 `skipped`。所有结果继续保持 `simulation_only=true`、`order_created=false` 和 `real_trading_allowed=false`。

## 验证

```powershell
D:\Anaconda\python.exe -m unittest tests.test_financial_graph tests.test_demo_financial_graph -v
```

专项测试覆盖正常链路、看空买入条件分支、C1 区间默认止损止盈、路线篡改拦截、Checkpoint 恢复、批量隔离和 20 份三类交付结果。

## Checkpoint 恢复

`FinancialGraphRuntime` 可选接收 `JsonCheckpointStore`。`run(query)` 首次执行，`run(resume=True)` 从失败节点继续。恢复演示故意让 Risk Manager 第一次抛错，实际调用次数为 `C1=1, Trader=1, Risk Manager=2`，证明已完成的研究和候选生成没有重复执行。演示默认使用临时 Checkpoint，成功后自动清理。

## 20 只真实股票验收

2026-08-10 使用默认 20 只银行股和“金融行业”真实板块完成同一批次运行：请求 20、完成 20、失败 0，`acceptance_20_met=true`。结果包含 20 份标准化投研报告、20 条交易建议和 20 份审计日志；默认只展示于终端并保留在返回值中，不生成文件。

真实运行还补齐了三类数据边界：任意行业分析会拉取足够大的板块比较集合；外部财务指标页失效时，从同源财务报表计算并标记 `derived:` 指标；个股确实没有研报时，记录 `not_available` 并让研报情绪贡献 0 分。网站不可用、超时等其他错误仍会失败，不会被中性值掩盖。

## 可见报告与文件输出

演示不再只把完整结果保存在 Python 变量中。终端默认展示十段标准化报告：基本信息、四 Agent 结论、来源时间、Bull/Bear 辩论、Synthesis、Trader 与条件路由、Risk Manager、Graph 审计、安全边界和输出状态。信号与状态使用“中文（英文代码值）”格式。默认不生成文件，避免连续运行产生大量报告。

只有显式添加 `--output-dir` 时才会生成：

```text
artifacts/c3/{symbol}-{mode}-financial-report.json
artifacts/c3/{symbol}-{mode}-audit-log.json
```

第一份保存完整 `result["report"]`，第二份保存顶层 Graph trace、节点 attempts、C1/Trader/Risk Manager trace 和 Harness trace。示例：`--output-dir artifacts/c3`。`artifacts/` 已被 Git 忽略，不会把本地运行报告提交到仓库。

## 当前状态

C3 正式任务已完成。真实交易继续关闭；回测、模拟撮合、费用和滑点由交付包四消费 C3 标准报告后完成，没有把执行能力倒塞进 C3。
