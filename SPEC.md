# 项目规范（SPEC）

## 1. 项目目标

构建一个可复用的 Agent 平台，并用证券金融分析作为复杂场景验证它的可靠性。平台需要支持：

- 单个 Agent 的多步运行、记忆和验证闭环；
- 多 Agent 的 Graph 编排、并行、条件分支和断点恢复；
- 输出结构化、数据可追溯、行为可约束、过程可审计；
- 在不接入真实下单的前提下完成分析、风控、模拟执行和回测。

## 2. 当前阶段：阶段一第二周

第 0 周的项目入口和第一周的 Echo/Harness 闭环已经完成。本周在 Harness 之上实现一个离线、确定性的 Loop，不接入金融数据、LLM API 或 Graph。

### 必须完成

1. 定义 `LoopState`，保存步数、历史响应和完成状态。
2. 实现 `LoopRunner.run(request)` 公共入口。
3. 让每个 Loop step 都经过已有的 `AgentHarness`。
4. 支持外部传入完成条件、最大步数和失败重试。
5. 失败时保留 Loop 状态、已有步骤结果、Loop trace 和原始异常。

### 明确不做

- 不在本周接入 AKShare、Tushare 或券商接口。
- 不在本周接入真实 LLM 或实现复杂的计划器、长期记忆。
- 不在本周实现 Graph/DAG、并行调度或 Checkpoint。
- 不把 API 密钥或账户信息写入项目。

## 3. 架构原则

```text
Model/Tools → Loop → Graph → Harness
                         ↑
                 Harness 贯穿并校验每一步
```

这里的 `Harness` 不是最后才添加的外壳，而是贯穿输入检查、工具调用、输出验证、日志追踪和人工确认的可靠性层。

## 4. 第一周公共接口

调用方只需要知道三个接口事实：

```python
request = AgentRequest(task="hello")
result = AgentHarness(EchoAgent()).run(request)
result.response.content  # "hello"
result.trace             # 有序的生命周期事件
```

- `Agent.run(request) -> AgentResponse`：Agent 的最小执行接口。
- `AgentHarness.run(request) -> HarnessResult`：统一的可靠性入口。
- `HarnessExecutionError.trace`：失败时读取已经发生的事件，不需要访问 Harness 内部状态。

当前实现不承诺持久化时间戳、分布式调度或长期记忆；这些属于后续 Graph 和工程化阶段。

## 5. 第二周公共接口

```python
runner = LoopRunner(
    AgentHarness(agent),
    completion_checker=lambda response: response.metadata.get("done", False),
    max_steps=3,
    max_retries=1,
)
result = runner.run(AgentRequest(task="complete the task"))
```

- `LoopState`：保存当前请求、已执行步数、响应历史和是否完成。
- `LoopRunner.run()`：重复执行有限步，并把每步交给 Harness。
- `completion_checker`：由调用方决定什么结果算完成。
- `LoopResult`：返回最终响应、最终状态、每步 Harness 结果和 Loop trace。
- `LoopExecutionError`：失败时暴露失败状态、已经完成的步骤和原始异常。

第二周的重试只针对 Harness 执行失败；达到 `max_steps` 时安全停止，不允许无限循环。

## 6. 后续阶段的成功标准

### 平台层

- `AgentHarness` 支持可插拔 Guardrail。
- Loop 支持计划、行动、观察、反思和记忆。
- Graph 支持并行、条件边、重试、超时和 Checkpoint。
- 非金融 demo 可以在两天内接入平台。

### 应用层

- 技术、基本面、行业、大盘四类 Agent 能输出结构化分析。
- 所有外部数据包含 `source`、`timestamp` 和 `as_of`。
- Trader 和 Risk Manager 输出建议，但不直接执行真实交易。
- 至少 20 只股票跑通端到端分析流程。

### 工程层

- 回测明确区分信号时间与执行时间，并计入交易成本。
- 具备日志追踪、失败恢复、质量评估和熔断能力。
- 能用实验数据比较 Harness 对幻觉率、无效调用和成功率的影响。

## 7. 当前验收命令

```powershell
python -m unittest discover -s tests -v
```

## 8. 安全底线

- `ALLOW_LIVE_TRADING` 默认必须为 `false`。
- 真实交易能力未列入当前交付范围。
- 数值指标、仓位限制和风险判断必须由确定性代码校验。
- 回测不得使用未来数据；报告要显示数据时间、信号时间和执行时间。
