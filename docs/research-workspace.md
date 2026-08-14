# 研究工作台

研究工作台把“分析一只股票”扩展为“持续管理多份研究结果”。现在已经覆盖股票自选、报告收藏、历史筛选、两份报告比较、状态提示以及普通版/专业版打印导出，P6 状态为 `done`。

## 客户能看到什么

客户首页新增“研究工作台”：

- 从上方股票目录把股票加入或移出“我的自选”；
- 点击自选股票可快速回到该标的的分析入口；
- 给重要的历史报告加星收藏，并只查看收藏报告；
- 从历史中选择两份已保存报告；
- 同一股票的两份报告显示为“同一股票前后变化”；
- 不同股票的报告显示为“两只股票横向比较”；
- 普通版展示综合判断、主要依据、主要风险、参考价格和研究区间；
- 专业版在相同事实之上增加四个研究维度的分数差；
- 查看报告是否仍在有效时间内，以及数据完整、来源降级或部分可用状态；
- 打印当前报告或比较结果，也可下载不依赖服务器的独立 HTML。

比较区域会同时显示两边报告的数据时间与归档时间。不同股票的绝对价格不会被解释成优劣。离线验证快照不会因为日期较早被误标为过期；真实最新报告超过七天会明确显示过期，但仍可阅读和追溯。

## 后端边界

`ResearchWorkspaceRuntime` 提供六个稳定入口：

- `snapshot()`：读取自选和可比较的冻结报告目录；
- `toggle_watchlist(symbol)`：加入或移出一只自选股票；
- `toggle_favorite(report_id)`：收藏或取消收藏一份冻结报告；
- `compare(left_report_id, right_report_id, view)`：比较两份不同的冻结报告。
- `export_report(report_id, view)`：生成单份普通版或专业版独立 HTML；
- `export_comparison(left_report_id, right_report_id, view)`：生成比较版独立 HTML。

正式运行使用 `JsonResearchWorkspaceStore` 保存非敏感的自选代码和收藏报告编号，测试使用 `InMemoryResearchWorkspaceStore`。偏好 Schema 已从 v1 兼容迁移到 v2。真正的报告继续由 P3 `AnalysisRepository` 保存；展示继续由 P4 `ReportViewRuntime` 生成。工作台不复制行情、Agent 结果或模型正文。

普通版导出只包含通俗研究摘要、价格区间、数据状态和免责声明。专业版在同一事实之上增加四维证据、来源、计划仓位上限、预计单次亏损和风险收益比。下载内容只在用户点击时生成并交给浏览器，不会自动在项目目录中产生大量文件。

## 安全与一致性

- 比较不会重新抓行情；
- 比较不会重新运行 Agent 或 Graph；
- 比较不会调用 DeepSeek，也不会产生 Token；
- 数字差值由 `Decimal` 确定性计算；
- 两边保留各自的 `report_id`、`snapshot_id`、`as_of` 和归档时间；
- 普通版和专业版的共享事实来自同一份冻结报告。
- 导出 HTML 会转义报告文本，不执行报告中的脚本或外部资源；
- 删除历史报告后，失效的收藏编号会在下次读取时自动清理。

## 当前验收

```powershell
D:\Anaconda\python.exe -m unittest tests.test_research_workspace -v
D:\Anaconda\python.exe -m unittest tests.test_dashboard.DashboardHttpTests.test_workspace_http_supports_favorite_and_both_export_depths -v
D:\Anaconda\python.exe Scripts\demo_product_acceptance.py
node --check src/agent_platform/web/client.js
```

专项测试覆盖自选加入/移出、v1/v2 JSON 持久化、未知股票拒绝、收藏、同股变化、跨股比较、专业维度、过期/部分数据状态、两种导出深度、HTTP 下载和无模型调用边界。项目完整回归为 375 项，产品整体验收通过。

自动化已经验证前端入口、后端 HTTP 下载和完整数据契约。当前开发环境无法让内置浏览器连接 Windows 主机的本地服务，因此没有把自动化结果冒充为真实鼠标点击；用户运行 Dashboard 后可按“分析并保存 → 星标收藏 → 收藏筛选 → 选择两份报告 → 比较 → 打印/导出”直接验收完整界面。
