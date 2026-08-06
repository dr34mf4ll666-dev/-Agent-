# MCP

存放外部数据和工具的适配层。每个适配器都必须明确数据来源、时间戳、错误处理和缓存策略。

`catalog.json` 是当前工具和外部 adapter 的管理清单，记录 interface、transport、密钥或来源策略、implementation 和 evidence。`active` 条目必须可以导入并拥有真实测试或演示；未经真实响应核对的条目只能是 `pending`。

当前已登记：

- 本地资料检索 Tool adapter：active，返回 `source`、`timestamp` 和 `as_of`。
- DeepSeek Model adapter：active，已完成离线错误测试和一次真实调用验证。
- OpenAI Responses adapter：active，已完成离线传输映射测试，未记录真实调用验收。
- 金融市场数据 MCP：pending，留给 B1 做最小真实字段、时间、权限和错误验证。

这里的 catalog 是 adapter 管理证据，不表示所有 active 条目都是 Model Context Protocol 服务器。真正的金融数据 MCP 仍属于 B1，不能用 Model adapter 或本地 Tool 冒充完成。
