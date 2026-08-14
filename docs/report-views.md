# 双层研究报告与互动图表

## 1. 目标

同一份证券研究报告同时服务两类读者：普通用户先看结论和风险，研究用户再按需查看指标与证据。两种阅读方式不是两套 Agent，也不会产生两份相互矛盾的结论。

## 2. 后端接口

`ReportViewRuntime.project(report_id, view)` 是唯一投影入口：

- `report_id` 必须指向 P3 SQLite 或内存 Repository 中已经成功归档的冻结报告；
- `view` 只允许 `basic` 或 `professional`；
- basic 和 professional 共用 `shared` 事实区及 `projection_fingerprint`；
- 投影过程只读，不访问外部数据源，不运行 Graph，不创建任务，不调用 LLM；
- 原始快照记录不会直接下发，页面只接收展示所需的行情、指标、来源和证据索引。

HTTP 入口为：

```text
GET /api/client/reports/{report_id}/view?view=basic
GET /api/client/reports/{report_id}/view?view=professional
```

不支持的视图会返回明确的 400 错误。

## 3. 普通版

普通版是首次使用的默认视图，面向不了解股票指标的用户。页面采用研究摘要结构，包含：

- 结论摘要；
- 主要依据；
- 主要风险；
- 关注区间。

内容由后端根据同一份确定性结果生成，不把“规则分数、风险收益比”等词直接交给新手，也不会把研究结论改写成买卖建议。页面只保留：

- 股票、最新价、涨跌幅、数据来源和数据时间；
- “准备数据→多维研究→风险检查→生成报告”四段通俗过程；
- 综合结论和一句摘要；
- 主要支持与主要风险；
- 支撑、当前参考和压力位置；
- 研究价格区间、预计风险、风险状态和免责声明。

普通版不显示 K 线、17 个节点、四 Agent 指标表、模型解读、数据健康详情、评分权重或后台工程术语。价格区间会直接解释为“接近下沿要更加留意风险；接近上沿不代表必然继续上涨”。

## 4. 专业版

专业版在同一事实区上增加：

- 四个 Agent 的分数、标签、摘要和评分权重；
- 17 个真实任务节点及完成、跳过等文字状态；
- 技术、基本面、行业和宏观的核心指标；
- 每个 Agent 使用的数据来源、`as_of` 和抓取时间；
- 统一快照的 14 类数据健康状态；
- 一致性、观点分歧、仓位和风控计算说明；
- 可展开的证据下钻卡片。

专业版仍是客户研究界面，不展示 Harness 配置、Checkpoint 路径、原始 trace、Prompt、Key 或后台运维入口。

## 5. 互动 K 线

图表数据由后端直接从冻结快照投影，浏览器不重新抓取行情：

- 日 K 与服务器聚合的周 K；
- 最近 20、40 或 60 根；
- SMA5、SMA20 和成交量独立开关；
- 鼠标十字线与 OHLCV 提示；
- 键盘左右方向键逐根查看；
- 涨跌状态同时使用文字图例，不只依赖颜色。

周线按自然周聚合：开盘取本周第一根，最高/最低取周内极值，收盘取最后一根，成交量求和。均线由确定性代码基于对应周期序列计算。

## 6. 一致性和安全边界

两种视图必须保持以下事实相同：

- `report_id`、`report_version`、`snapshot_id`；
- 综合结论、四维分数和证据一致性；
- 研究价格区间、仓位、预计风险和风控状态；
- `simulation_only=true`、`order_created=false`、`real_trading_allowed=false`。

视图选择只保存在浏览器 `localStorage`，不会写回报告或数据库。切换视图后，历史报告数量和模型调用记录不应增加。

## 7. 验收

专项测试：

```powershell
D:\Anaconda\python.exe -m unittest tests.test_report_views tests.test_dashboard tests.test_client_app tests.test_product_acceptance -v
```

完整产品验收：

```powershell
D:\Anaconda\python.exe Scripts\demo_product_acceptance.py
```

页面验收路径：启动客户前台，打开一份已完成报告。普通版确认专业指标隐藏；切换专业版，确认 17 个节点、4 个 Agent 证据、日/周 K、区间和指标开关可用；再切回普通版，确认报告号、快照号和结论未改变，也没有新增历史报告。
