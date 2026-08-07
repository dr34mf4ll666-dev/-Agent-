# B1 腾讯 A 股日线数据 Tool

## 这一切片解决什么问题

平台此前只有人工模拟 K 线。本切片增加第一个真实金融数据入口：通过 AKShare 的腾讯证券接口读取 A 股不复权日线，再转换成项目已有的 `MarketDataSeries`。调用方统一使用 `DailyMarketDataTool.run(arguments)`，不直接依赖 AKShare、Pandas 或腾讯字段。

本文记录 B1 的第一个真实数据切片及当时的字段验证过程。后续 B1 已补齐周线、分钟线、实时行情、资金流、财务、估值、宏观、行业、舆情、缓存、限流、硬总超时、Tushare 第二来源和 MCP Server；完整最终状态见 `financial-data-hub.md`。

## 为什么从东方财富切换到腾讯

2026-08-07 的最小真实验证中：

- 本机使用 AKShare 1.18.64、Pandas 2.3.3；
- `stock_zh_a_hist` 请求东方财富 `push2his.eastmoney.com` 时出现 `RemoteDisconnected`，联网环境下又出现 `ProxyError`；
- 用户说明东方财富可能已限制当前 IP，因此本切片不继续重试东方财富；
- `stock_zh_a_hist_tx` 成功返回 `sz000001` 在 2024-01-02 至 2024-01-05 的 4 根真实历史日线。

腾讯成功响应的实际列为：

```text
date, open, close, high, low, amount
```

这和当前 AKShare 文档列出的 8 列不同。已安装版本的源码也明确把腾讯数组前 6 列命名为上述字段，因此实现按真实版本做严格映射，字段缺失或变化时返回 `schema_mismatch`，不会静默猜测。

## 成交量字段为何要乘以 100

腾讯返回的第六列虽然被 AKShare 1.18.64 命名为 `amount`，但它不是成交额。以 2024-01-02 为例：

| 数据 | 数值 |
| --- | ---: |
| 腾讯 `amount` | 1,158,366 |
| 同日新浪 `volume` | 115,836,645 股 |
| 腾讯值乘以 100 | 115,836,600 股 |

其他三个交易日也呈现同样关系，因此本切片把腾讯 `amount` 解释为按“手”记录的成交量，并乘以 100 转成项目统一的“股”。腾讯值只能恢复到整手，和另一来源相比可能缺少不足一手的尾数；该精度限制保留在 fixture 元数据中。

## 数据流

```text
Tool arguments
  → DailyBarQuery 参数与范围校验
  → AkShareTencentDailyAdapter / JsonDailyMarketDataAdapter
  → 腾讯字段映射和成交量单位转换
  → MarketDataSeries 金融不变量校验
  → 带 source、timestamp、as_of 和 trace 的字典输出
```

`codebase-design` 的深模块原则影响了这里的边界：AKShare 被封装在一个小 adapter 后面，真实网络和离线 JSON 实现同一个 `fetch(query, policy)` interface，调用方只看稳定 Tool。

## 时间语义

- `as_of`：行情本身对应的时刻。腾讯只返回交易日期，项目把 A 股日线映射为当日 `15:00:00+08:00`。
- `timestamp`：本次数据获取时刻，使用带时区的 Asia/Shanghai 时间。
- `source`：真实 adapter 使用 `akshare.stock_zh_a_hist_tx`；离线结果顶层标记 `offline_fixture`，每根回放 K 线仍保留原始 provider 来源。

15:00 是项目的日线时间映射规则，不是腾讯响应里自带的时区字段。

## 查询与安全边界

当前查询只接受：

- 带市场前缀的代码，例如 `sz000001`、`sh600000`；
- `YYYYMMDD` 格式的开始和结束日期；
- 最长 366 天的区间；
- 不复权数据，`adjust` 必须为空。

限制区间是为了约束 AKShare 的分年请求数量。这个首切片的 `timeout_seconds` 只传给每次上游 HTTP 请求，不是整个 AKShare 函数调用的硬总时限；后续统一 `FinancialDataHub` 已通过可终止子进程补齐整个调用的硬总超时。

## 运行方式

默认离线回放真实样本，不访问网络：

```powershell
python Scripts\demo_market_data.py
```

显式调用腾讯真实接口：

```powershell
python Scripts\demo_market_data.py --live --symbol sz000001 --start-date 20240102 --end-date 20240105 --attempts 1
```

若尚未安装金融可选依赖：

```powershell
python -m pip install -e ".[finance]"
```

## 稳定错误

- `dependency_missing`：实时模式缺少 AKShare；
- `provider_unavailable`：腾讯请求超时、断连或其他传输错误；
- `empty_response`：指定条件没有 K 线；
- `schema_mismatch`：真实字段变化或数值无法转换；
- `fixture_error`：离线文件损坏或不满足契约。

真实 provider 失败会保留原始异常为 `cause`，但上层只依赖稳定错误码。重试次数限定为 1–3 次，并在 trace 中记录每次开始、成功或失败。

## 首切片边界与后续结果

- 本文中的 `DailyMarketDataTool` 仍是保留的腾讯日线专用 adapter；统一调用入口已经迁移到 `FinancialDataHub`。
- 实时行情、分钟线、资金流、财务、估值、宏观、行业和舆情已覆盖，并通过只读 MCP Server 暴露。
- JSON TTL 缓存、provider 限流、有限重试和跨进程硬总超时已经完成。
- Tushare 第二日线来源已经真实返回 4 条记录，19 个 dataset 均有真实离线样本。
- 复权价格仍是 B1 明确不做的边界；当前接口只支持不复权数据。
