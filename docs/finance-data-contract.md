# 金融行情数据契约

阶段二先统一数据语义，再实现分析逻辑。调用方通过两个接口使用本模块：

```python
bar = MarketBar.from_mapping(record)
series = MarketDataSeries.from_records(records)
```

## 字段语义

- `symbol`：证券代码；一个 `MarketDataSeries` 只允许一个代码。
- `open/high/low/close`：使用 `Decimal` 保存的价格。
- `volume`：大于等于零的整数成交量。
- `source`：这条记录的数据来源。
- `as_of`：行情本身对应的时间，例如日线收盘时间。
- `timestamp`：系统获取或生成这条记录的时间。

`as_of` 和 `timestamp` 必须包含时区，且 `as_of <= timestamp`。后续分析和回测应使用 `as_of` 判断当时可见的数据，不能用 `timestamp` 混淆行情发生时间。

## 数据不变量

- 所有价格必须是有限正数。
- `high` 不得低于 `open`、`low` 或 `close`。
- `low` 不得高于 `open`、`high` 或 `close`。
- 时间序列不能为空、不能混合证券代码，也不能出现倒序或重复 `as_of`。

## 离线 fixture

`tests/fixtures/synthetic_market_bars.json` 是人工构造的练习数据，`dataset_type` 和每条记录的 `source` 都标记为 `synthetic_fixture`。它不代表真实证券、真实行情或投资收益。
