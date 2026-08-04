# Rule

存放 Agent 的行为边界和安全规则，例如数据必须带来源、禁止未经确认的真实下单、工具权限最小化。

当前 Harness 已实现五类内置 Guardrail：JSON Schema 校验、来源校验、限流、关键词阻断和确定性代码交叉验证。实现位于 `src/agent_platform/core/guardrails.py`，配置、错误语义和限制见 `docs/harness-guardrails.md`。

规则文档描述“必须满足什么”，Python 模块负责强制执行。新增规则时必须同时补充允许、拒绝和错误配置测试。
