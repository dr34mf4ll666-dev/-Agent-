# P1 异步分析任务中心

## 1. 已解决的问题

客户分析不再占用一个 HTTP 请求同步等待完整 C3。网页提交证券和数据模式后立即得到任务编号，随后查询真实节点；只有任务完整成功才能读取报告。失败、超时、取消和服务重启都保留明确状态。

## 2. 稳定 interface

`AnalysisJobRuntime` 对外提供五个 interface：

- `submit(request)`：创建任务并立即返回；
- `get(job_id)`：读取任务、错误和节点状态；
- `retry(job_id)`：从 Checkpoint 继续失败任务，只执行未完成节点；
- `cancel(job_id)`：请求在下一个安全进度点停止；
- `result(job_id)`：只允许成功任务读取报告。

线程 Worker、容量、任务总超时、JSON 原子保存、启动恢复、错误归一化、两层 Graph Checkpoint 和协作取消都隐藏在 module 内部。默认并行两个任务、保留 64 项记录、单项总时限 180 秒。

## 3. 真实节点进度

页面展示 17 个可核验节点：

1. C1 总流程与 Planner；
2. 技术、基本面、行业和宏观四个 Specialist；
3. 证据汇总、多空辩论、一致性/偏差检查和综合结论；
4. Trader、市场条件路由、Risk Manager、弱市阻断和 Finalize；
5. 行情图表整理与客户报告。

状态来自 `GraphRunner` 和 C1 的真实执行事件，不用定时器伪造。条件分支未选择时显示“无需执行”；重试会显示尝试次数。

## 4. HTTP 流程

```text
POST /api/client/jobs
GET  /api/client/jobs/{job_id}
POST /api/client/jobs/{job_id}/retry
POST /api/client/jobs/{job_id}/cancel
GET  /api/client/jobs/{job_id}/result
```

创建和重试返回 HTTP 202。网页每 500 毫秒查询一次状态，并在 `sessionStorage` 保存当前任务编号。

## 5. 失败恢复和重启恢复

- Specialist Graph 与 C3 Graph 分别保存原子 Checkpoint。
- 节点失败时，已完成节点及其状态继续保留。
- 点击“只重试失败步骤”后，Graph 从 Checkpoint 执行失败或未完成节点，不重复已完成节点。
- 任务元数据和完整成功报告保存在 `.runtime/analysis_jobs/jobs.json`。
- 服务启动时会自动恢复原先 `queued/running` 的任务；成功报告在重启后仍可读取。
- API Key、账户信息和真实订单不会写入任务存储。

Checkpoint 只解决任务执行恢复；完整历史检索、数据库迁移和报告比较仍属于 P3 SQLite 历史模块。

## 6. 超时和取消语义

任务总时限默认为 180 秒。超时后任务立即变为可重试失败状态，迟到结果会被丢弃。Python 线程无法安全强杀已经进入第三方库的阻塞调用，因此底层调用可能在后台自然返回；这不会覆盖任务的超时结论。运行中主动取消同样在下一个安全进度点生效，不返回半份报告。

## 7. 验收

```powershell
D:\Anaconda\python.exe Scripts\demo_analysis_jobs.py
D:\Anaconda\python.exe -m unittest tests.test_analysis_jobs tests.test_client_app tests.test_dashboard -v
D:\Anaconda\python.exe Scripts\run_dashboard.py
```

客户页面应显示任务编号、17 个节点、条件分支、停止按钮；故障任务显示“只重试失败步骤”。完整自动化还覆盖成功结果门禁、总超时、迟到结果丢弃、JSON 报告恢复和启动自动续跑。
