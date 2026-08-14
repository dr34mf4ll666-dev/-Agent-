# P7 模型治理与真实质量门禁

## 1. 这项能力解决什么问题

接入大模型以后，项目不能只记录“调用成功”。还需要知道：本次用了哪个版本的 Prompt 和输出 Schema，调用是否超出预算，是否命中过缓存，模型不可用时是否仍能给出安全结果，以及一个新模型配置是否真的通过了固定评测。

P7 把这些约束放在统一的 `ModelGovernanceRuntime` 和 `LLMQualityGateRuntime` 中。它们只管理语言模型的解释和候选文字，不接管金融计算。指标、价格区间、仓位、止损止盈和真实交易开关仍由确定性代码控制。

## 2. 治理运行时

`ModelGovernanceRuntime.generate(request, operation=...)` 包装已有 `ModelGateway`，对每次模型调用执行：

- 记录 `policy_version`、`prompt_version`、`schema_version` 和 `route`；
- 在真正访问供应商前检查最大调用次数、预计 Token 和单次输出上限；
- 对成功结果做带 TTL 的内存缓存，缓存键包含版本、Prompt、Schema 和业务操作；
- 记录实际输入/输出/总 Token，并在实际总量超预算时拒绝结果进入缓存；
- 保留原 Gateway 的 provider、model、response id、耗时和 trace；
- 供应商失败、Key 不可用或预算拒绝时转入本地固定格式解释。

当前客户解释、后台助手和动态辩论的真实路由都可以通过这个治理 seam 接入。固定评测特意关闭缓存，避免把重复命中误写成模型稳定性。

治理元数据示例：

```json
{
  "policy_version": "p7-policy-v1",
  "prompt_version": "client-explanation-prompt-v1",
  "schema_version": "client-explanation-schema-v1",
  "route": "deepseek",
  "cache_hit": false,
  "degraded": false,
  "calls_used": 1,
  "tokens_used": 128
}
```

## 3. 客户前台能看到什么

客户前台智能解读卡片会显示：

- 使用 DeepSeek，还是本地安全解释；
- 本次模型名、Token 和解释版本；
- 是否发生降级以及简短原因；
- “有帮助/没帮助”反馈按钮。

反馈只保存到当前报告对应的 SQLite 记录中，保存内容包括反馈类型、报告号、解释版本、provider、model 和治理元数据，不保存 API Key、Prompt 或授权信息。接口为：

```text
POST /api/client/feedback
```

反馈不是重新分析，也不会改变报告中的分数、价格区间、仓位和风控结果。

## 4. 团队后台能看到什么

后台 `/admin` 新增“模型治理”面板，直接读取 `GET /api/governance`，展示两条当前模型路由：

- 客户智能解读；
- 后台项目助手。

每条路由显示 provider、model、策略/Prompt/Schema 版本、调用次数预算、Token 预算、缓存数量和当前是否降级。面板下方显示固定评测状态、原始结果是否留存和候选版本是否允许晋级。没有真实评测文件时会明确显示“待真实评测”和“暂不可晋级”，不会把离线 Mock 涂成通过。

面板只展示安全运行元数据，不展示 API Key、Prompt、完整模型输入输出或授权头。点击“刷新治理状态”只读取当前内存计数和本地评测摘要，不会触发模型调用。

## 5. 质量门禁与回滚

`LLMQualityGateRuntime.evaluate(report, policy=...)` 不重新计算评测指标，而是检查评测报告是否同时满足：

- 固定评测本身通过；
- 在要求真实模型时，报告必须标记 `live=true`；
- 原始逐次结果被保留；
- 每次结果都通过证据和安全边界检查；
- 声明的接受阈值全部通过；
- provider 和 model 字段完整。

`ModelReleaseRegistry` 只允许 `can_promote=true` 的候选版本成为默认版本，并保留上一版本用于显式回滚。离线 Mock 可以证明门禁和回滚逻辑，但在 `require_live=true` 时不能通过，不能把 Mock 质量当成真实 DeepSeek 质量。

## 6. 离线验收

查看版本、缓存、预算拒绝和安全边界：

```powershell
D:\Anaconda\python.exe Scripts\demo_llm_governance.py
```

运行固定动态辩论评测的离线版本：

```powershell
D:\Anaconda\python.exe Scripts\demo_dynamic_debate_evaluation.py
```

离线结果会明确显示：固定评测链路可以通过，但“真实模型运行”门禁不会通过。这是预期行为。

## 7. 真实 DeepSeek 固定评测

使用自己的有效 Key 运行相同评测集：

```powershell
D:\Anaconda\python.exe Scripts/demo_dynamic_debate_evaluation.py --live
```

命令默认把逐次原始结果覆盖保存到唯一固定路径；如需另存一份，可显式指定输出路径：

```powershell
D:\Anaconda\python.exe Scripts/demo_dynamic_debate_evaluation.py --live --output .runtime\llm-evaluation\deepseek-fixed-v1.json
```

真实固定评测已完成 4 次运行并通过质量门禁：候选/最终证据有效率 100%、正反平衡率 100%、重试率 0%、降级率 0%、结果稳定性 100%，共消耗 7911 Token。最新逐次原始结果位于 `.runtime/llm-evaluation/deepseek-fixed-v1.json`。没有有效 Key 时仍只能运行离线治理和 Mock 门禁测试，不能把 Mock 结果当作真实模型质量结论。

## 8. 安全边界

- LLM 只能解释已有确定性结果，或在证据目录中提出受约束的语言候选。
- LLM 不能写入指标、估值、价格区间、仓位、止损止盈和交易状态。
- 模型不可用时，本地解释和固定辩论仍可返回，确定性报告不被阻断。
- `simulation_only=true`、`order_created=false`、`real_trading_allowed=false` 继续保持不变。
