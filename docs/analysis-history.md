# P3 SQLite 持久化与历史报告

## 用户能看到什么

客户首页新增“最近分析”。每张历史卡片显示股票、当时结论、任务状态、数据版本、数据时点和报告版本。点击卡片会重新打开当时冻结的完整报告，包括 K 线、四个研究维度、统一 `snapshot_id`、价格区间和风险结果；这个动作不重新访问数据源，也不重新运行 Agent Graph。

普通打开或刷新页面不会自动创建分析任务、写入历史或调用模型；只有用户点击“开始分析”才创建新任务。若浏览器保存着尚未完成的任务编号，页面仍会按 P1 规则自动恢复轮询。

每张卡片右上角提供单条删除，“最近分析”标题区提供“清空历史”。两种操作都会先显示二次确认；后端还要求不同的确认标记，不能只靠浏览器按钮保护。确认后会级联删除报告、任务归档、快照、四 Agent、Graph 和模型调用，并清理对应的 P1 已完成任务记录与 Checkpoint。运行中的任务不能通过历史入口删除。

## 模块边界

`AnalysisRepository` 是稳定接口，提供原子归档、列出最近报告、读取单份报告、追加模型调用元数据、删除单条和清空历史。`SQLiteAnalysisRepository` 是正式 adapter，`InMemoryAnalysisRepository` 是测试 adapter。P1 的 JSON 和 Checkpoint 继续负责排队、续跑与失败恢复；P3 只保存成功后的长期历史，避免把执行恢复与查询历史塞进同一个模块。

一份成功报告在同一 SQLite 事务中保存：

- 任务请求、17 个节点状态、重试和恢复状态；
- P2 完整统一快照及 `snapshot_id`；
- 技术、基本面、行业、宏观四个 Agent 结果；
- Specialist Graph、C3 Graph 与任务阶段状态；
- 客户冻结报告、`report_id` 和 `report_version`；
- DeepSeek 或本地解释的 provider、model、Token、耗时和状态。

归档失败时事务整体回滚，任务不会被标记为成功，也不会暴露半份结果。报告正文、辩论上下文和快照带 SHA-256 完整性校验；被外部修改或损坏时读取会明确失败。敏感字段名和疑似密钥值会在入库前被拒绝，Prompt、API Key、Tushare Token 和授权头不进入数据库。

## 迁移与并发

数据库使用 SQLite `PRAGMA user_version` 显式版本化，当前 schema 为 v2；v2 在 v1 基础上增加可复用的模型输出正文与调用类型。每次操作使用独立短连接、外键约束、WAL 和 `BEGIN IMMEDIATE`，支持本机多个分析线程并发写入。相同任务生成稳定报告编号，重复归档相同内容是幂等的；相同编号试图覆盖不同内容会被拒绝。

## 验收

无需网络、无需生成报告文件的直观演示：

```powershell
D:\Anaconda\python.exe Scripts\demo_analysis_history.py
```

专项测试：

```powershell
D:\Anaconda\python.exe -m unittest tests.test_analysis_repository tests.test_analysis_jobs tests.test_dashboard -v
```

覆盖显式迁移、服务重启恢复、不同报告不覆盖、20 路并发写入、事务中途失败回滚、数据库内容损坏拒绝、模型调用恢复和敏感 Key 拒绝。浏览器验收还会检查历史卡片、点击重开、报告/快照编号、无脚本错误及 375px 手机布局。
