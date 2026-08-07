# MCP

存放外部数据和工具的适配层。每个适配器都必须明确数据来源、时间戳、错误处理和缓存策略。

`catalog.json` 是当前工具和外部 adapter 的管理清单，记录 interface、transport、密钥或来源策略、implementation 和 evidence。`active` 条目必须可以导入并拥有真实测试或演示；未经真实响应核对的条目只能是 `pending`。

当前已登记：

- 本地资料检索 Tool adapter：active，返回 `source`、`timestamp` 和 `as_of`。
- DeepSeek Model adapter：active，已完成离线错误测试和一次真实调用验证。
- OpenAI Responses adapter：active，已完成离线传输映射测试，未记录真实调用验收。
- 腾讯 A 股日线 Tool adapter：active，已用 4 根真实历史日线验证字段、单位、时间、错误和离线回放。
- 金融 Data Hub：active，19 个 dataset 共用缓存、限流、重试、硬超时、统一错误和离线回放。
- 金融 MCP Server：active，使用官方 MCP Python SDK 注册两个只读 stdio 工具，并通过 `call_tool` 测试。
- 完整金融市场数据 MCP 验收：active，AKShare 与 Tushare 均有真实成功调用，19 个 dataset 全部具备真实最小样本和离线回放。

这里的 catalog 同时管理普通 adapter 和真正的 MCP Server；通过 `transport` 字段区分。金融 MCP Server 只提供查询能力，不包含下单或真实交易工具。
