# A3 Loop Engineering

## 完成范围

A3 在原有有限步 Loop 和认知闭环上补齐四组能力：三层记忆、三类触发循环、任务工作目录隔离，以及 Skill/项目约定/任务上下文注入。所有演示使用确定性 Python Agent，不调用真实模型或真实交易。

## 认知执行主线

```text
Trigger
  ├─ Heartbeat / Cron
  ├─ Hook Event
  └─ Recursive Goal
          ↓
LoopDispatcher ── 去重台账 ── JSON Snapshot
          ↓
独立 TaskWorkspace
          ↓
ContextInjector
  ├─ SkillContext
  ├─ 项目约定
  ├─ 任务上下文
  ├─ 选中的项目记忆
  └─ 选中的组织记忆
          ↓
CognitiveLoopRunner
  Plan → Action → Observation → Reflection
          ↓
受控 ToolRegistry + Harness + WorkingMemory
```

## 三层记忆

### 工作记忆

- 生命周期：当前认知任务；
- 写入：Loop 自动记录 Plan、Action、Observation、Reflection 摘要；
- 容量：固定上限，FIFO 淘汰；
- 持久化：版本化工作记忆快照；
- 用途：帮助 Agent 依据最近结果修正当前任务。

### 项目记忆

- 生命周期：跨多次 Loop 运行；
- 命名空间：`MemoryScope.PROJECT + project_id`；
- 写入：必须显式调用 `upsert`，不会自动永久保存完整对话；
- 内容：项目事实、决定、产物索引和项目约定；
- 查询：按 key、类别或文本确定性筛选。

### 组织记忆

- 生命周期：跨项目；
- 命名空间：`MemoryScope.ORGANIZATION + organization_id`；
- 内容：团队惯例、共享规则和经过确认的偏好；
- 隔离：项目命名空间不能读取其他项目或其他组织的数据。

`LongTermMemory` 统一实现项目和组织记忆的 `upsert/query/delete` 接口。每条记录保留 `source`、带时区的创建/更新时间和递增 revision。`InMemoryLongTermMemoryStore` 用于测试，`JsonLongTermMemoryStore` 用于离线跨运行恢复。

## 上下文注入

`ContextInjector` 只组合调用方明确提供的内容：

- `SkillContext`；
- 项目约定；
- 当前 `AgentRequest.context`；
- 指定 key 的项目记忆；
- 指定 key 的组织记忆。

长期记忆不会默认全部灌入 Agent。调用方必须列出要注入的 key，生成的 `InjectedContext` 会递归冻结映射和序列。Planner 通过 `request.context["injected_context"]` 读取，Action 和 Reflection 通过 `state.context` 读取。`injected_context` 和 `task_workspace` 是平台保留键，调用请求不能自行覆盖。

## 任务隔离

`TaskWorkspaceManager` 在配置根目录下按运行 ID 创建或重开独立目录。`TaskWorkspace.resolve()` 只接受安全相对路径，拒绝绝对路径和 `..` 路径逃逸。

当前选择“独立工作目录”而不是 Git worktree，因为调度任务只需要文件状态隔离，不需要为每次运行创建 Git 分支。目录默认位于 `.runtime/`，不提交到仓库。

## 三类触发循环

### Heartbeat / Cron

`HeartbeatLoop.tick()` 根据 anchor 和间隔计算时间槽；同一时间槽只执行一次。`CronLoop.tick()` 使用五字段 Cron 表达式，支持 `*`、列表、范围和步长。Cron 的星期字段使用 0/7 表示星期日。

调度采用确定性 tick 接口，不在库内部启动永久后台线程。宿主进程负责定期调用 tick；这样测试无需真实等待，进程重启后也能用同一配置和运行台账继续去重。

### Hook

`HookLoop` 只处理名称匹配的 `HookEvent`。同一个 subscription 和 event_id 组成幂等键，重复投递不会重复执行。事件 payload 作为只读、JSON 兼容的任务上下文注入。

### 递归目标

`GoalLoop` 使用调用方的确定性 decomposer 把目标拆成子目标，先完成子目标，再把子结果交给父目标。`max_depth` 和 `max_goals` 是硬停止边界。稳定的 run_id 和目标路径组成持久去重键，恢复时跳过已经完成的目标。

## 持久运行台账

三类触发方式共用 `LoopRunLedger`：

- 保存 run_id、幂等键、触发方式、任务、开始/结束时间；
- 保存成功输出或稳定失败文本；
- 保存独立工作目录；
- 使用版本 1 JSON 快照恢复；
- 损坏、字段不符或版本不兼容时明确拒绝。

台账不是 Agent 知识记忆。它回答“任务是否执行过”，项目/组织记忆回答“以后还需要知道什么”。

## 当前边界

- Cron/Heartbeat 是可持久去重的调度内核，不包含常驻 Windows 服务或分布式调度集群；
- 独立工作目录提供文件隔离，不提供进程、容器或操作系统权限隔离；
- 长期记忆是确定性 key/类别/文本查询，不包含向量数据库或语义检索；
- A3 不包含真实 Model Gateway，真实模型接口属于 A4。

## 运行演示

```powershell
python Scripts\demo_loop_engineering.py
```

演示会运行 Heartbeat、Cron、Hook 和一个包含两个子目标的递归目标流程。六次任务全部经过同一 `CognitiveLoopRunner`、三层记忆、上下文注入和独立工作目录，并把运行结果保存在 `.runtime/a3-loop-engineering/`。
