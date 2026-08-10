# C2 Risk Manager 与完整交易建议链

## 完整接口

`C2TradingRuntime.run(C2TradingQuery)` 是完整 C2 的统一入口。调用方提供一份 C1 报告和模拟账户风控上下文，模块内部依次执行 Trader 与 Risk Manager：

```text
C1 研究报告
    ↓
Trader：buy / sell / hold 模拟候选
    ↓
Risk Manager：仓位、回撤、时段、流动性、价格和确认检查
    ↓
模拟执行许可或阻断结果（不会创建订单）
```

Trader 与 Risk Manager 也可以单独运行，并都支持 Graph 节点入口。Risk Manager 在 Agent 执行前通过专属 Harness Pre-Flight 计算全部十项硬规则，执行后再用 Schema 和 CrossValidator 重算完整结果。

## 风控上下文

`RiskContext` 包含：

- 模拟账户权益、当前仓位和请求目标仓位；
- 除当前股票外的同一行业暴露；
- 当前组合回撤；
- 平均每日成交额；
- 带 `+08:00` 时区的评估时间；
- 止损价、止盈价和人工确认状态。

这些是模拟账户输入。即使使用 `--live` 获取真实市场数据，账户权益、仓位、回撤和人工确认仍由演示参数提供，不代表连接了真实券商账户。

## 硬规则

1. 单笔最大亏损为账户权益的 2%。买入仓位上限按 `2% ÷ 止损距离百分比` 计算。
2. 单行业最终暴露不超过 30%，当前股票可用上限为 `30% - 其他同业暴露`。
3. 组合回撤超过 15% 时禁止新增仓位；已有仓位目标强制降低 50%。
4. A 股模拟执行时间限制为工作日 09:30–11:30 和 13:00–15:00，时间必须带 `+08:00`。
5. `bearish`、`bear` 或 `risk_off` 环境禁止新增买入仓位。
6. 平均每日成交额至少为 1000 万元；单账户目标名义金额最多使用平均每日成交额的 10%。
7. 买入必须满足 `止损价 < 参考价 < 止盈价`，且收益风险比不低于 1.5。
8. 最终批准仓位超过 10% 时必须有显式人工确认。
9. 最终仓位还要同时服从 C1 Market Regime Gate，实际取所有仓位上限中的最小值。
10. `simulation_only=true`、`order_created=false`、`real_trading_allowed=false` 永远不能被模型或调用方覆盖。

## 决策状态

- `approved`：规则通过，可以进入后续模拟执行。
- `adjusted`：请求仓位被风险上限调低后通过。
- `pending_human_confirmation`：超过 10%，等待显式确认。
- `blocked`：交易时间、市场环境、流动性或价格控制不通过。
- `forced_reduction`：总回撤超过 15%，强制降低已有仓位。
- `no_action`：Trader 为持有，或风险调整后不增加仓位。

批准仅表示允许后续模拟执行。Risk Manager 本身不会创建模拟订单，更不会创建真实订单。

## 运行

```powershell
# 默认停在人工确认
D:\Anaconda\python.exe Scripts\demo_c2_trading.py

# 显式模拟人工确认，完整通过风控
D:\Anaconda\python.exe Scripts\demo_c2_trading.py --confirm

# 真实市场数据 + 模拟账户风控上下文
D:\Anaconda\python.exe Scripts\demo_c2_trading.py --live --confirm --symbol sz000001
```

可通过 `--drawdown 16` 验证强制减仓，通过 `--sector-exposure-other 28` 验证行业上限，通过 `--evaluation-time` 验证交易时段。完整参数可执行 `--help` 查看。

## 验证

```powershell
D:\Anaconda\python.exe -m unittest tests.test_risk_manager tests.test_demo_c2_trading -v
```
