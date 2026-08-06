# A5 非金融资料研究 Demo

## 目标

A5 用一个与证券无关的“本地资料研究”任务验证平台通用性：输入主题和一组本地文档，输出带证据编号的结构化摘要。它不是另写一套 Agent 框架，而是把新领域的资料契约、工具和提示词接到现有平台接口上。

默认模式使用确定性 Mock，不访问网络、不需要 API Key。`--live` 只是在同一 `ModelGateway` 接口下换成 DeepSeek，Loop、Graph、Harness 和工具代码不变。

## 运行链路

```text
主题 + 本地资料
      ↓
CognitiveLoopRunner
  Plan → Action → LocalDocumentSearchTool → Observation → Reflection
      ↓
GraphRunner（YAML 工作流）
  retrieve → organize → synthesize
      ↓
AgentHarness
  报告 Schema → 来源检查 → 证据编号交叉验证
      ↓
带来源、时间和 evidence_id 的结构化摘要
```

三个 Graph 节点的职责分开：

- `retrieve`：模型通过认知 Loop 规划和选择动作，只能调用 `ToolRegistry` 中的 `local_document_search`；工具结果经过 Schema 和来源校验。
- `organize`：确定性代码去重并生成 `E1`、`E2` 等证据编号，不让模型改写原始引文和来源。
- `synthesize`：模型只根据已整理证据写摘要；Harness 检查报告结构、报告来源、主题一致性，以及每条结论引用的证据编号是否真实存在。

Graph 的两条边都在 `Workflow/examples/non_financial_research.yaml` 中声明输出和输入 Schema。每个节点完成后写入 Checkpoint；恢复时只重新执行失败节点。

## 默认离线运行

在项目根目录执行：

```powershell
D:\Anaconda\python.exe Scripts\demo_non_financial_research.py
```

演示会输出 Graph 节点顺序、Loop 步数、允许工具、工作记忆条目、证据、报告来源、模型调用次数、token 和 Graph trace。

验证故障恢复：

```powershell
D:\Anaconda\python.exe Scripts\demo_non_financial_research.py --verify-recovery
```

该模式先让 `synthesize` 产生一次预期故障，再从 Checkpoint 恢复。恢复阶段的节点调用计数应为：

```text
{'retrieve': 0, 'organize': 0, 'synthesize': 1}
```

这表示已经完成的检索和证据整理没有重复执行。

## 可选 DeepSeek 模式

只在当前 PowerShell 临时设置密钥，然后显式运行：

```powershell
$env:DEEPSEEK_API_KEY="你的密钥"
D:\Anaconda\python.exe Scripts\demo_non_financial_research.py --live
```

密钥不会从文件读取，也不应写入 `.env`、源码、测试或 Git。实时模式会产生四次模型调用：Plan、Action、Reflection 和报告综合，因此会消耗真实额度。切换模型供应商不会放宽本地 Schema、工具允许列表或证据交叉验证。

## 新领域最小接入记录

A5 在 2026-08-06 的一次开发阶段内完成，低于“两天内完成最小接入”的验收上限。新增内容只有领域层和装配层：

1. `src/agent_platform/research/`：文档契约、本地检索工具、模型 Agent 和工作流装配。
2. `Workflow/examples/non_financial_research.yaml`：三节点 Graph、边 Schema 和可靠性配置。
3. `tests/fixtures/research_documents.json`：带 `source`、`timestamp`、`as_of` 的离线资料。
4. `Scripts/demo_non_financial_research.py`：离线、实时和 Checkpoint 恢复入口。
5. `tests/test_non_financial_research.py` 与 `tests/test_demo_non_financial_research.py`：正常、拒绝和故障恢复测试。

A5 没有修改 `src/agent_platform/core/` 的 Harness、Loop、Graph、Checkpoint、记忆或 Model Gateway 语义。若接入另一个领域，最小步骤仍是“定义领域契约 → 注册受控工具和节点 → 配置 Schema/Guardrail → 提供 Mock 与测试”，无需复制调度框架。

## 当前边界

- 只检索固定本地资料，不包含网络搜索、向量数据库或语义召回。
- 离线 Mock 用来验证工程链路，不代表模型质量评测。
- Checkpoint 应按任务使用独立路径；不要用旧主题的 Checkpoint 恢复新主题。
- 报告只能证明引用编号存在，不能自动判断自然语言结论是否完整等价于原文；更严格的蕴含评估属于后续 Evaluator 范围。
