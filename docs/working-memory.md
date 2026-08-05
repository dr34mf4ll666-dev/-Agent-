# 工作记忆最小闭环

## 定位

工作记忆只服务于当前一次认知任务。它保存最近的 Plan、Action、Observation、Reflection 和必要事实，让 Agent 在选择下一步时不必遍历或重新解释所有原始对象。

它不是项目知识库，也不是组织级共享记忆。本切片不包含向量检索、语义相似度、跨任务知识沉淀或多人权限。

## 稳定契约

每条 `MemoryEntry` 包含：

- `sequence`：严格递增的任务内序号；
- `kind`：`plan`、`action`、`observation`、`reflection` 或 `fact`；
- `summary`：长度受调用端限制的可读摘要；
- `step`：对应认知 Loop 步数；
- `data`：只允许 JSON 兼容的少量结构化字段。

`WorkingMemoryView` 是 Agent 能读取的不可变视图。`CognitiveLoopState.memory` 在选择 Action 前包含 Plan，在 Reflection 前包含最新 Observation。

## 容量和淘汰

`WorkingMemory(capacity=N)` 最多保存 N 条。写入第 N+1 条时，按 FIFO 淘汰最早条目，并增加 `dropped_count`。序号不会复用，因此从快照恢复后仍能判断真实先后关系。

容量限制避免长期 Loop 的内存和提示词上下文无限增长。当前不做“重要性评分”或自动摘要合并，以保持行为确定、可测试。

## 快照和恢复

`WorkingMemorySnapshot` 使用版本 1 契约，保存容量、下一序号、淘汰数量和现存条目。提供两个 store adapter：

- `InMemoryWorkingMemoryStore`：测试和临时进程；
- `JsonWorkingMemoryStore`：UTF-8 JSON 原子写入。

Loop 每新增一条记忆就保存完整快照。恢复时先由 store 校验字段、版本、序号和 JSON 数据，再调用 `WorkingMemory.restore(snapshot)`。损坏、缺字段、未知版本或非 JSON 数据会被明确拒绝。

## 与认知 Loop 的关系

```text
Plan ────────────────→ WorkingMemory
                           ↓
Agent.choose_action(state.memory)
       ↓
Action → Tool → Observation ─→ WorkingMemory
                                  ↓
Agent.reflect(state.memory, observation)
       ↓
Reflection ────────────────→ WorkingMemory → JSON Snapshot
```

完整的 actions、observations 和 reflections 仍保留在 `CognitiveLoopState`，便于审计；工作记忆是有容量边界的近期视图，不替代审计历史。

## 运行演示

```powershell
python Scripts\demo_working_memory.py
```

演示第一次用错误类型调用工具，随后 Agent 从工作记忆看到失败 Observation 并修正参数。容量设为 5，共写入 7 条，因此最早两条被淘汰；最终从 JSON 快照恢复剩余记忆。
