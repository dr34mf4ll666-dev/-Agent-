# 面试展示版：可信度、可复现与可靠性证据

这一轮优化解决三个面试展示问题：

1. 一份报告的数据是否完整，来源和时间能不能说清楚；
2. 数据源失败、缓存降级、输出不合规或任务中断时，系统怎么处理；
3. 两次分析结果不同，到底是行情变了，还是规则、主数据或解释策略变了。

## 数据质量

`DataQualityRuntime.evaluate(snapshot, security_record, now)` 只读取快照的摘要和证券主数据，不保存完整行情记录。每个数据集都会输出：

- 来源状态：主源、备用源、缓存、验证快照或不可用；
- `source`、获取时间 `timestamp` 和数据对应时间 `as_of`；
- 完整、降级、不可用或字段无效；
- 缺失原因和用户可执行的处理建议。

整体状态只有三种正式结果：

- `complete`：关键数据完整，可以与同样完整的报告比较；
- `degraded`：使用备用来源、缓存或缺少非关键数据，结果可以查看但比较要谨慎；
- `blocked`：关键数据缺失或时间/来源契约无效，不把它当作可靠比较依据。

历史报告如果由旧版程序生成，读取不会失败，而是显示“历史报告，来源版本未知”，并把 `comparison_ready` 设为 `false`。

## 运行指纹

每份新报告都会保存：

```text
snapshot_id
security_master_version
code_version
config_version
model_policy_version
report_version
```

以上字段先规范化 JSON，再计算 SHA-256。指纹是“这次运行使用了哪些版本输入”的标识，不是 API Key，也不包含 Prompt、授权头或完整行情。SQLite 迁移到 schema v4，新表只保存质量摘要、版本输入和指纹。

## 报告比较

同一证券的两份报告会生成 `change_reasons`，目前区分：

- 行情或数据时间变化；
- 数据源状态变化；
- 证券主数据变化；
- 确定性规则变化；
- 大模型解释策略变化。

不同证券则明确标注为横向比较，不把价格高低误认为同一证券的前后变化。

## 离线可靠性演示

运行：

```powershell
D:\Anaconda\python.exe Scripts\demo_interview_showcase.py
```

这条命令不联网、不写报告文件，固定展示五个场景：

1. 正常运行；
2. 数据源超时后有限重试；
3. 首选来源失败后的缓存降级；
4. 从 Checkpoint 恢复且不重复成功节点；
5. 输出校验失败后的安全拒绝。

终端会显示成功率、故障恢复率、重试/降级/缓存统计、重复节点数、模型调用与 Token 统计，以及 P50/P95/P99 耗时。实验数据是固定离线证据，不等同于线上 SLA。

同一入口也打印一组由报告比较逻辑生成的差异原因。管理员后台的“面试展示 · 可信度与可靠性”入口调用同一个脚本；客户前台不暴露 Harness、Checkpoint 或原始运行日志。

## 验收重点

```powershell
D:\Anaconda\python.exe -m unittest tests.test_analysis_provenance tests.test_reliability_experiment tests.test_demo_interview_showcase
node --check src/agent_platform/web/client.js
```

前台验收时：

- 普通版应看到“本次分析可信度”；
- 专业版应看到数据质量明细和运行指纹；
- 分析结束后“正在形成研究报告”区域应消失；
- 两份历史报告比较结果应出现“为什么不同”。

真实 Chromium 前台验收：

```powershell
D:\Anaconda\python.exe Scripts/e2e_dashboard.py
```

它会实际提交两次离线分析，验证可信度卡片、专业版指纹、加载区域收起、手机宽度无横向溢出和比较差异说明；不会使用实时行情或 DeepSeek。
