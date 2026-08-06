# docs

存放项目架构、设计决策、数据契约、运行流程和阶段性复盘文档。

正文文档应说明背景、约束、输入输出和验收方式；临时笔记不要替代 `SPEC.md`、`checklist.json` 或 `progress.txt`。

当前专项文档：

- `finance-data-contract.md`：金融行情字段、时间和来源语义。
- `harness-guardrails.md`：五类 Guardrail、配置注册、错误语义和 trace。
- `cognitive-loop.md`、`working-memory.md`、`loop-engineering.md`：认知闭环、三层记忆、调度和上下文注入。
- `graph-engineering.md`、`langgraph-mapping.md`：Graph 配置、可靠性和框架映射。
- `model-gateway.md`：Mock、OpenAI 和 DeepSeek 的统一模型 interface。
- `non-financial-research-demo.md`：A5 跨领域接入、证据约束和恢复验证。

根目录 `dev-map.md` 负责把九类 Harness 组件、核心 module、接入 seam 和验证入口连起来。
