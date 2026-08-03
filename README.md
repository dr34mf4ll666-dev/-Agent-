# 通用 Agent 平台及证券金融分析应用

这是一个以证券分析为验证场景的通用 Agent 平台实践项目。目标不是让 LLM 直接“炒股”，而是验证一套可编排、可验证、可恢复、可审计的 Agent 工程骨架。

## 当前状态

当前已完成第三周：平台已经具备确定性的 DAG 执行、条件分支、状态合并，以及基于 JSON Checkpoint 的失败恢复。尚未接入金融数据和真实 LLM。

核心关系：

```text
Harness 负责可靠性与审计
    └── Graph 负责多 Agent 编排
            └── Loop 负责单个 Agent 的多步运行
```

## 快速验证

在项目根目录运行：

```powershell
python -m unittest discover -s tests -v
```

后续安装开发依赖后，也可以运行：

```powershell
python -m pytest
```

## 项目结构

```text
.
├── SPEC.md                 # 当前可执行的项目规范与验收边界
├── AGENTS.md               # 协作者和 Agent 的工作约定
├── checklist.json          # 功能清单与证据索引
├── progress.txt            # 按日期记录的进度日志
├── docs/                   # 架构和设计说明
├── Rule/                   # 行为边界和 Guardrail 规则
├── Skill/                  # 可复用技能和标准动作
├── Workflow/               # Graph/DAG 工作流定义
├── Scripts/                # 自动化验证和辅助脚本
├── MCP/                    # 外部数据与工具适配层
├── SubAgents/              # 专业 Agent 定义
├── src/agent_platform/     # Python 平台代码
│   └── core/               # 契约、Harness、Loop、Graph 和 Checkpoint
└── tests/                  # 自动化测试
```

## 推进方式

每个阶段都必须留下四类结果：可运行代码、自动化测试、文档说明、进度证据。先用离线 fixture 和 mock 验证流程，再接入真实数据；真实交易在整个项目中保持关闭，只做模拟执行和回测。

## 第 0 周的完成定义

- 项目规范、目录职责和安全边界已经写明。
- 功能清单能区分已完成、进行中和待办事项。
- 最小 Python 包可以被测试发现。
- 骨架测试可以在本地离线运行。

## 第一周的完成定义

- `AgentRequest`、`AgentResponse` 和 `Agent` 接口已经确定。
- `EchoAgent` 可以在没有网络和模型的情况下运行。
- `AgentHarness` 可以执行前置检查、Agent 调用、后置检查和 trace 记录。
- 空任务、错误输出和 Agent 异常都会被拦截或保留失败 trace。
- Guardrail 可以通过接口注入，而不需要修改 Harness 主流程。

## 第二周的完成定义

- `LoopState` 能保存当前步数、历史响应和完成状态。
- `LoopRunner` 会让每一步都经过 `AgentHarness`。
- Loop 支持外部传入完成条件，不把业务判断写死在运行器里。
- Loop 支持最大步数、失败重试和重试耗尽后的错误追踪。

## 第三周的完成定义

- `GraphDefinition` 用节点和有向边描述一个无环工作流。
- `GraphRunner` 按拓扑顺序运行节点，并合并节点返回的状态更新。
- 条件边只激活满足条件的分支；未选中的节点会标记为 `skipped`。
- 图在执行前检查入口、边端点和环，非法图不会执行任何节点。
- `JsonCheckpointStore` 在节点完成、跳过或失败后保存状态。
- 失败恢复会从 Checkpoint 继续，不重复运行已经完成的节点。
