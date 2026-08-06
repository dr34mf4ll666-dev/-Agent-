# SubAgents

存放专业 Agent 的角色定义、输入输出 Schema、工具权限和验收标准。

`catalog.json` 保存当前可运行 Agent 卡片。每张 active 卡片必须声明 role、interface、implementation、工具权限、模型权限、Guardrail 和 evidence；项目测试会验证实现可以导入，证据文件真实存在。

当前 active Agent：

- `echo`：不调用模型和工具，只验证最小 Agent/Harness interface。
- `gateway-research-planner`：通过 Model Gateway 完成 Plan、Action 和 Reflection，只允许本地资料检索工具。
- `gateway-research-reporter`：只根据整理后的证据综合报告，并接受结构、来源和证据编号检查。

技术、基本面、行业、大盘、综合研判、交易和风控 Agent 仍属于后续金融交付包。没有实现、测试和权限记录前，不在 catalog 中标记为 active。
