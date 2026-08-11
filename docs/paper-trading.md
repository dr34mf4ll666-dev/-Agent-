# D4 本地持续模拟交易

## 这部分解决什么问题

C3 到 Risk Manager 为止只会输出研究结论、候选动作和批准仓位，并且明确 `order_created=false`。D4 第一切片补上“批准之后如何安全地做模拟执行”：它读取 C3 标准报告，复核报告没有被篡改，再用确定性代码计算本地模拟成交和账户变化。

这不是实盘接口。代码没有券商凭据、下单 API 或真实订单对象，所有成交都标记为 `broker=local_simulator`、`real_order=false`。

## 一次运行的流程

1. C3 运行四类 Specialist、辩论、Trader、条件路由和 Risk Manager。
2. `PaperTradingRuntime` 复核 C3 内部一致性以及三项安全字段。
3. 离线复现从技术分析证据读取收盘价；真实运行另取腾讯 `market.realtime` 执行报价。两种报价都必须携带 `source`、`timestamp` 和 `as_of`，且禁止使用未来时点行情。
4. 如果风控要求人工确认但没有确认，只记录等待状态，不模拟成交。
5. 如果允许执行，按目标仓位、账户权益和 100 股整手计算调仓数量。
6. 本地撮合计入双边佣金、最低佣金、卖出印花税和滑点，更新现金与持仓。
7. 把本轮结果和复盘摘要原子写入同一份 JSON 账本。

## 账本里保存什么

一份 session 文件包含：

- 固定 session 配置和交易安全边界；
- 现金、持仓、平均成本和已实现盈亏；
- 每轮 C3 决策摘要、行情来源、模拟订单和安全字段；
- 人工确认人、结果、备注和时间；
- 失败类型、原因、是否恢复和恢复备注；
- 每轮复盘统计及真实行情覆盖天数。

写入使用临时文件替换，避免正常写入中断后只留下半份 JSON。重复 `cycle_id` 会被拒绝，session 开始后不能悄悄修改初始资金、股票范围或费用参数。

## 直接验收

离线模拟成交，不产生持久文件：

```powershell
D:\Anaconda\python.exe Scripts\demo_paper_trading.py --confirm
```

人工确认门禁：

```powershell
D:\Anaconda\python.exe Scripts\demo_paper_trading.py
```

开始或继续真实行情 session：

```powershell
D:\Anaconda\python.exe Scripts\demo_paper_trading.py --live --confirm --session-id d4-live --ledger .runtime\paper_trading\d4-live.json
```

只查看已有账本，不运行分析、不访问网络：

```powershell
D:\Anaconda\python.exe Scripts\demo_paper_trading.py --review-only --ledger .runtime\paper_trading\d4-live.json
```

`.runtime/` 已被 Git 忽略。只有显式传入 `--ledger` 才持久保存，避免普通演示制造许多结果文件。

## 当前完成边界

当前代码、测试和离线界面已经证明本地撮合、确认门禁、失败记录、恢复标记、持久账户和安全边界可用。它没有证明系统已经连续运行了一周。

2026-08-10 的真实最小验证取得腾讯实时报价 11.2900，`as_of=2026-08-10T16:14:27+08:00`。当时已不在 A 股交易时段，C3 输出 hold，本地撮合如实记录 `no_action`，没有为了展示成交而绕过时段风控。

账本仍只把 `mode=live` 计入真实日期，并保留“至少 7 个日历日且 5 个不同交易日”的原始统计规则。2026-08-11 用户明确要求不再等待该自然时间，因此 D4 调整后验收把周期项记为 `waived_not_proven`：T4.3 可以完成，但界面、总纲和最终报告都不得把单日记录描述成长周期稳定性证明。
