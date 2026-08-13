# P2 统一分析快照与数据源健康

## 1. 解决什么问题

P2 之前，一次客户分析由四个 Agent 分别读取数据，Graph 完成后 K 线图还会再请求一次日线。即使缓存常常让第二次请求很快，这两次读取仍可能来自不同获取时刻，也无法用一个编号证明报告、图表和解释消费的是同一批数据。

P2 新增不可变 `AnalysisSnapshot`。`AnalysisSnapshotRuntime.acquire(query)` 先收集一次分析需要的 14 类唯一数据请求，再冻结来源、获取时间、数据时点、缓存状态和降级说明。四个 Agent 通过只读 `SnapshotFinancialDataTool` 消费这份快照；K 线图直接复用技术 Agent 使用的同一份 `market.daily`。

## 2. 稳定 interface

```python
snapshot = snapshot_runtime.acquire(combined_query)
financial_tool = snapshot.tool()
mapping = snapshot.to_mapping(include_records=False)
```

- `acquire(query)`：主源、备用源、最近可追溯缓存和不可用状态都在 module 内处理。
- `tool()`：返回与原 `FinancialDataTool` 相同调用形状的只读 adapter；请求不在快照中会被拒绝，不能绕过快照临时取数。
- `to_mapping()`：用于 Checkpoint 保存；`include_records=False` 用于客户页面，只展示健康与追溯信息，不重复发送全部原始记录。

## 3. 数据源策略

- `primary`：实时主源本次成功。
- `backup`：主源失败后，由真实备用源接管。目前日线在配置 Tushare Token 时可由 Tushare 接管。
- `cache_fresh`：Data Hub 的 TTL 缓存命中。
- `cache_stale`：主源和备用源都失败，使用最近一次可追溯成功数据，并在报告中明确标记。
- `fixture`：离线已验证快照。
- `not_available`：非关键数据无法取得，保留明确缺失状态；现有确定性 Agent 决定是否使用中性降级。

日线、财务报表、估值、行业和核心宏观数据属于关键输入，所有来源都失败时终止分析，不生成看似完整的报告。资金流和机构研究属于允许部分缺失的数据，缺失不能冒充真实 0 值。

## 4. P1 恢复关系

异步任务首次运行时把完整快照原子保存到该任务的 Checkpoint 目录。失败重试或服务重启恢复时读取原快照，不重新抓数据，因此已完成节点和未完成节点始终属于同一 `snapshot_id`。P3 仍负责 SQLite 长期历史、迁移、检索与报告比较；P2 不提前实现这些能力。

## 5. 直观验收

```powershell
D:\Anaconda\python.exe Scripts\demo_analysis_snapshot.py
```

输出会同时显示主源成功、备用源接管、历史缓存降级、非关键数据部分缺失和关键数据阻断。默认不联网、不生成文件。

客户页面完成分析后，会在证券摘要下方显示“统一数据快照”：统一数据时点、短快照编号、可用数据数量，以及每类数据的真实主源、备用源、缓存、验证快照或暂不可用状态。

专项测试：

```powershell
D:\Anaconda\python.exe -m unittest tests.test_analysis_snapshot tests.test_client_app tests.test_dashboard -v
```

真实交易边界保持不变：`simulation_only=true`、`order_created=false`、`real_trading_allowed=false`。
