# D2 Harness 工程化总验收

## 完成范围

D2 在已有可观测面板上补齐四类工程能力：独立 Evaluator、运行级熔断告警、Agent 最小工具权限，以及稳定入口和可复现交付。固定 Harness 对比实验也一并完成，因此 T4.2 不再保留缺口。

## 稳定 interface

Python 调用方只需要一个入口：

```python
from agent_platform.d2_engineering import D2EngineeringRuntime

report = D2EngineeringRuntime.from_files().run()
assert report.passed
```

安装项目后可以使用稳定 CLI：

```powershell
agent-platform d2-verify
```

在仓库中无需安装 console script 也可以运行：

```powershell
D:\Anaconda\python.exe Scripts\demo_d2_engineering.py
```

两个入口调用同一个 `D2EngineeringRuntime`，默认只打印终端结果，不生成报告文件。`--config` 和 `--dataset` 可替换 JSON；键名、版本、类型、重复 Agent、重复用例和不安全阈值都会在执行前被拒绝。

## 独立 Evaluator

`IndependentEvaluator` 只读取随包发布的固定数据集，不读取 Agent 自评分。当前数据集包含来源约束、结构化完成状态、工具权限和金融措辞安全 4 类用例，确定性检查：

- 预期事实是否完整且精确匹配；
- 是否出现错误或无证据事实；
- 是否执行允许列表外的工具/API；
- 是否出现禁用措辞；
- 是否端到端完成、是否经过恢复；
- 整体耗时和 Token 成本。

固定评分为事实 60 分、完成 20 分、无幻觉 10 分、无越权工具 10 分。被评估 Agent 无法通过声称“自己得了高分”改变结果。

## 运行级熔断和告警

`IndustrialHarness.run(...)` 在 operation 外层统一执行权限检查和连续失败计数。默认配置连续失败 3 次后：

1. 熔断状态变为 `open`；
2. 返回 `agent_circuit_opened` critical 告警；
3. 后续请求在 operation 执行前返回 `circuit_open`，即真正暂停；
4. 60 秒后只放行一次 half-open 探测，成功才关闭并清零，失败则重新打开。

当前告警是可审计的结构化 `HarnessAlert`，由调用方接到日志、邮件或监控平台；项目不会在未授权情况下向外部发送消息。熔断状态当前保存在进程内，服务重启后会重置，这一点不冒充持久化分布式熔断。

## Agent 最小工具权限

默认配置登记 9 个当前 Agent。Echo、Reporter、Trader 和 Risk Manager 等不需要工具的 Agent 使用空列表；四个金融 Loop 和 Research Planner 只能访问各自唯一工具。

五个实际使用 ToolRegistry 的 Runtime 已传入 Agent 名称和集中权限表。注册阶段和每次 dispatch 都会复核权限；未知 Agent 默认拒绝，越权工具在调用其 `run()` 前被阻断。`SubAgents/catalog.json`、包内 JSON 配置和代码默认表由测试强制保持一致。

## Harness 对比实验

两组使用同一 4 个任务、同一固定数据、同一脚本化模型首轮输出。无 Harness 组直接接受首轮结果；有 Harness 组实际经过 `AgentHarness` 的输入/输出规则，失败后使用同一 fixture 中的固定恢复输出。

| 指标 | 无 Harness | 有 Harness |
| --- | ---: | ---: |
| 幻觉率 | 80.00% | 0.00% |
| 无效工具/API 调用 | 1 | 0 |
| 端到端成功率 | 25.00% | 100.00% |
| 平均耗时 | 40.00ms | 86.25ms |
| Token 总成本 | 80 | 120 |
| 失败恢复成功率 | 无恢复样本 | 100.00% |

这个结果证明了本项目固定规则链路的行为，不代表 DeepSeek、OpenAI 或其他真实模型的线上质量。结果同时显示可靠性并非免费：本 fixture 中平均耗时增加 46.25ms，Token 增加 40。未来若改用真实模型，必须在相同模型版本、温度、任务和数据下重新实验。

## 可复现交付

- `requirements.lock` 固定 D2 核心/测试依赖版本；金融实时数据额外依赖仍由 `finance` extra 管理。
- `.github/workflows/ci.yml` 在 Windows Python 3.11 安装锁定依赖、运行稳定 CLI 和完整 unittest。
- 本地已验证 CLI/Demo、JSON 配置、Python 编译和完整测试；GitHub Actions 是否成功要以推送后的远端运行结果为准，不能由本地提前宣称。

真实交易继续关闭。本阶段没有券商连接、订单创建或账户写入。
