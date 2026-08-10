# C2 Trader 模拟候选信号

## 目标

`TraderRuntime.run(TraderQuery)` 消费一份已经通过校验的完整 C1 报告，把研究结论转换为 `buy`、`sell` 或 `hold` 模拟候选信号。它不会重新运行四个 Specialist，也不会创建订单。

```text
C1 综合研究报告
      ↓
校验 C1 状态、目标区间和质量检查
      ↓
确定性 Trader 信号规则
      ↓
Schema + 候选结果重算 Harness
      ↓
交给后续 Risk Manager
```

## 信号规则

- `buy`：综合倾向为 `positive` 或 `cautious_positive`，四 Agent 加权评分不低于 20，且证据一致性置信度不低于 60。
- `sell`：综合倾向为 `negative` 或 `cautious_negative`，四 Agent 加权评分不高于 -20，且置信度不低于 60。
- `hold`：方向、评分和置信度没有同时越过买入或卖出阈值。

这些规则由确定性代码执行。LLM 不能改变信号、目标区间、仓位上限或安全字段。

## 输出

Trader 输出以下信息：

- 模拟候选动作及触发规则；
- 继承自 C1 的目标价研究区间和证据一致性置信度；
- Market Regime、风险偏好和 C1 门控后的研究仓位上限；
- C1 的来源列表，以及技术、基本面、行业和宏观报告各自的 `timestamp` 与 `as_of`；
- 是否需要 Risk Manager 和人工确认；
- Harness trace 和 Trader 阶段 trace。

当候选动作是 `buy` 或 `sell`，且 C1 给出的研究仓位上限超过 10% 时，Trader 会保守地标记 `human_confirmation_required=true`。这只是提前标记，实际仓位和最终人工确认仍由 Risk Manager 强制执行。

## 运行

```powershell
D:\Anaconda\python.exe Scripts\demo_trader.py
D:\Anaconda\python.exe Scripts\demo_trader.py --live --symbol sz000001
```

默认使用真实验证样本离线回放；只有显式使用 `--live` 才访问真实数据接口。

## Trader 层安全边界

- `simulation_only` 固定为 `true`；
- `order_created` 固定为 `false`；
- `real_trading_allowed` 固定为 `false`；
- 所有可行动候选必须进入 Risk Manager；
- Trader 本身不计算单笔 2% 风险、行业 30% 上限、总回撤 15%、交易时间、流动性、止损止盈或最终仓位；这些检查现在由下游 `RiskManagerRuntime` 完成。

完整 C2 的风控规则和运行方式见 `docs/risk-manager.md`。

## 验证

```powershell
D:\Anaconda\python.exe -m unittest tests.test_trader_runtime tests.test_demo_trader -v
```
