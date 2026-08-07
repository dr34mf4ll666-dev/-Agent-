# B1 金融 Data Hub 与 MCP Server

## 目标

`FinancialDataHub.fetch(dataset, params, mode)` 是 B1 对 Agent 暴露的统一 interface。调用方不需要了解腾讯字符串、AKShare DataFrame、Tushare token、中文列名或具体网站；Data Hub 在内部完成真实 provider 调用、字段映射、数值规范化、缓存、限流、有限重试、硬总超时、错误归一化和离线回放。

`FinancialDataTool.run(arguments)` 把同一能力接入现有 `ToolRegistry`，`create_financial_mcp_server()` 又把它暴露为官方 MCP Python SDK 的只读工具。真实交易不在该 interface 中。

## 支持的数据集

| Dataset | 内容 | 主要真实来源 | 真实状态 |
| --- | --- | --- | --- |
| `market.daily` | A 股日线 | AKShare 腾讯 | 已成功 |
| `market.weekly` | 由真实日线按周确定性聚合 | AKShare 腾讯 | 已成功 |
| `market.minute` | 1/5/15/30/60 分钟行情 | AKShare 新浪 | 已成功 |
| `market.realtime` | 单股实时报价 | 腾讯单股接口 | 已成功 |
| `market.fund_flow` | 个股资金流 | AKShare 同花顺 | 已成功 |
| `fundamental.balance_sheet` | 资产负债表 | AKShare 新浪 | 已成功 |
| `fundamental.income_statement` | 利润表 | AKShare 新浪 | 已成功 |
| `fundamental.cash_flow` | 现金流量表 | AKShare 新浪 | 已成功 |
| `fundamental.indicators` | ROE、资产负债率等 | AKShare 新浪 | 已成功 |
| `fundamental.valuation` | PE、PB、PS | 腾讯报价 + 新浪年报 | 已成功 |
| `macro.index` | 指数日线 | AKShare 新浪 | 已成功 |
| `industry.snapshot` | 行业/板块快照 | AKShare 新浪 | 已成功 |
| `macro.gdp` | 中国 GDP 年率 | AKShare 金十 | 已成功 |
| `macro.shibor` | Shibor 利率曲线 | AKShare 金十 | 已成功 |
| `macro.policy_lpr` | LPR 政策利率 | AKShare 东方财富宏观 | 已成功 |
| `sentiment.news` | 财经新闻摘要 | AKShare 财新 | 已成功，曾触发硬超时后重试成功 |
| `sentiment.announcements` | 公司公告 | AKShare 巨潮资讯 | 已成功 |
| `sentiment.research` | 券商研报 | AKShare 东方财富研报 | 已成功 |
| `tushare.daily` | Tushare 第二日线来源 | Tushare `daily` | 已成功，4 条真实日线 |

东方财富日线和资金流在 2026-08-07 的真实验证中被当前网络主动断开，因此没有作为主实现；同日东方财富宏观 LPR 和个股研报接口可以正常返回。Data Hub 使用腾讯、新浪、同花顺、东方财富、金十和巨潮资讯分散单一来源风险。

## 统一输出

每次返回 dataset、source、timestamp、record_count、cache_hit、mode、records 和 trace。每条 record 都有 subject、fields、source、timestamp 和 as_of。

金融数值统一保存为十进制字符串，后续确定性指标代码使用 `Decimal` 解析，避免经过 JSON 时引入二进制浮点误差。

## 时间语义与限制

- 行情 `as_of` 使用 K 线结束时间或腾讯报价时间。
- 行业快照、资金流和财新摘要没有独立发布时间字段，因此 `as_of` 使用抓取完成时间。
- 财务报表 `as_of` 当前表示报告期末，不等于公告真正公开时间。历史回测必须再与公告发布时间连接，否则可能产生未来数据泄漏。
- `timestamp` 始终是 provider 抓取完成时间。
- 所有时间必须带时区，并满足 `as_of <= timestamp`。

## 可靠性实现

真实结果写入已被 Git 忽略的 `.runtime/finance/data_cache.json`。缓存键由 dataset、params 和 source 共同计算。实时报价默认缓存 15 秒，财务报表默认 1 天，其他数据按变化速度设置 TTL。

同一进程默认每个 provider 每 60 秒最多 5 次请求，超限立即返回 `rate_limited`。默认最多尝试 2 次。真实 provider 在独立子进程运行，超出整个调用的总时限后会被操作系统终止并返回 `timeout`。

稳定错误包括：`invalid_request`、`unknown_dataset`、`auth_required`、`permission_denied`、`rate_limited`、`timeout`、`provider_unavailable`、`empty_response`、`schema_mismatch`、`cache_error` 和 `fixture_error`。

## 离线回放

默认模式是 `offline`，使用 `tests/fixtures/financial_data_hub.json`。全部 19 个 dataset 都来自最小真实验证；Tushare 日线样本包含 2026-08-03 至 2026-08-06 的 4 条真实记录。

```powershell
D:\Anaconda\python.exe Scripts\demo_financial_data_hub.py
D:\Anaconda\python.exe Scripts\demo_financial_data_hub.py --live --dataset market.realtime --symbol sz000001
```

重复运行相同日线请求时应看到 `cache_hit=True`。

## MCP Server

项目使用官方 `mcp` Python SDK 1.x，注册两个只读工具：`list_financial_datasets` 和 `get_financial_data`。

```powershell
D:\Anaconda\python.exe Scripts\run_financial_mcp.py
```

MCP 测试不仅核对工具名称，还通过 server 的 `call_tool` 真正调用离线 `macro.gdp`，验证结构化结果可以返回。

## Tushare 验收结果

当前环境安装 Tushare 1.4.29。验证过程先确认无权限时 Data Hub 会稳定返回 `permission_denied`；账号取得权限后，`tushare.daily` 成功返回平安银行 4 条真实日线，证明认证、权限、参数、字段映射和时间语义全部可用。

复现真实调用时，在已设置 `TUSHARE_TOKEN` 的 PowerShell 中运行：

```powershell
$env:TUSHARE_TOKEN="你的 token"
```

```powershell
D:\Anaconda\python.exe Scripts\demo_financial_data_hub.py --live --dataset tushare.daily --ts-code 000001.SZ --start-date 20260801 --end-date 20260807 --attempts 1
```

LPR 和研报不依赖 Tushare 付费研报权限，分别通过 `akshare.macro_china_lpr` 和 `akshare.stock_research_report_em` 完成真实验证。Token 只从进程环境读取，不能写入 `.env`、fixture、日志或 Git；真实样本不包含 Token。
