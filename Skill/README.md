# Skill

存放可复用的标准动作和技能，例如指标计算、财务数据清洗和报告格式化。

技能必须登记在 `catalog.json`，并明确 interface、implementation、输入、输出和 evidence，不能只依赖自然语言描述。`tests/test_project_bootstrap.py` 会验证 active 条目的实现可以导入，证据文件真实存在。

当前 active Skill：

- `controlled-task`：通过 `SkillContext` 和 `ContextInjector` 把只读操作约定注入 Plan、Action 和 Reflection；A3 Loop 演示提供运行证据。
- `local-research-summary`：使用 A5 的资料研究 module 输出带证据摘要；离线演示、恢复测试和接入文档提供运行证据。

新增 Skill 时先复用已有 module 的稳定 interface。只有输入、输出、实现和验证入口都明确后，才能把状态设为 `active`。
