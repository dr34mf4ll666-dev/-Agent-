# 统一可观测记录与中文面板

## 目标

D2 第一切片把 Harness、Graph 和 Model Gateway 原本不同形状的运行结果统一成一份观测契约，让终端面板或未来 Web UI 不需要分别理解三套内部实现。

本切片直接回答四个问题：一次请求经过了哪些步骤、是否失败、整次运行耗时多久、模型消耗了多少 Token。多次记录还会汇总总失败率、平均/P95/最大耗时和分层指标。

## 接口

```python
record = ObservationAdapter.from_execution(
    run_id="model-001",
    execution=model_gateway_result,
    started_at=started_at,
)
report = ObservabilityDashboard.build([record])
panel_data = report.to_mapping()
```

- `ObservationAdapter.from_execution(...)` 自动识别 `HarnessResult/Error`、`GraphResult/Error` 或 `ModelGatewayResult/Error`，归一化运行层、组件、状态、事件、尝试次数、错误和 Token。
- `ObservationRecord` 表示一次执行。失败记录必须保存错误消息，Token 总数必须等于输入与输出之和，时间必须包含时区。
- `ObservabilityDashboard.build(...)` 只读取统一记录，计算总览和 Harness/Graph/Model 分层指标；重复 `run_id` 会被拒绝，避免同一次运行被重复计数。
- `ObservabilityReport.to_mapping()` 提供稳定字典，可直接交给后续 CLI、API 或 Web UI，不要求写本地报告文件。

## 时间语义

Model Gateway 已经返回调用耗时和 Token，因此 adapter 可以直接保留。Harness 和 Graph 目前只保存有序 trace，调用方必须提供实际测得的整次 `duration_ms`。

旧 trace 没有单事件时间戳，本切片明确将其标记为 `event_timing=ordered_only`。界面只显示事件顺序，不把均分或推测时间冒充为真实节点耗时。后续若在运行器内部增加时间戳，可以继续扩展统一契约而不改变业务 Agent。

## 直观验收

```powershell
D:\Anaconda\python.exe Scripts\demo_observability.py
```

演示完全离线，不写报告文件：

1. 运行一次成功 Harness；
2. 运行一次因空任务被 Pre-Flight 拒绝的 Harness，验证失败 trace 和原因；
3. 实际运行一个两节点 Graph；
4. 运行确定性 Mock Model Gateway，产生 21 个 Token 和 125ms 模拟 provider 延迟；
5. 汇总 4 次运行、1 次失败和 25% 失败率，并逐条打印三层调用链。

看到“结论: 本切片验收通过”表示调用链、Token、耗时、失败率和错误保留五项均通过。

## 当前边界

这只是 T4.2 的可观测切片，不代表 D2 已完成。独立 Evaluator、运行级连续失败熔断与告警、每个 Agent 的最小工具白名单、稳定 CLI/API 与 CI 仍需后续实现。Harness 对比实验属于随后量化阶段。
