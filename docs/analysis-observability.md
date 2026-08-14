# P5 统一分析可观测性

## 目标

P5 把原先分散在任务进度、Graph trace、数据快照、模型响应和数据库中的运行信息串成同一条分析链路。每次客户分析在提交时生成一个 `trace_id`，这个编号随任务结果和历史报告保存。客户失败页用它联系维护人员，团队后台用它定位慢节点、失败点、数据降级和模型成本。

P5 复用既有 D2 可观测能力的语义，不修改金融结论，也不建立第二套业务执行流程。D2 继续负责 Harness、Graph 和 Model Gateway 的工程验收记录；P5 面向真实客户分析生命周期，增加跨层关联、持久化、统计和网页投影。

## 稳定接口

`AnalysisObservabilityRuntime` 隐藏 trace 存储、脱敏、聚合和页面投影，只公开以下操作：

- `begin(...)`：登记一项分析及其安全请求元数据；
- `span(...)`：新增或更新一个结构化运行步骤；
- `finish(...)`：结束整条链路并保存客户可操作错误；
- `trace(...)`：读取一项分析的瀑布链路；
- `overview(...)`：读取整体可靠性指标；
- `remove_job(...)`：历史报告删除时同步清理对应 trace。

正式环境使用 `JsonAnalysisTraceStore` 原子保存到 `.runtime/observability/analysis_traces.json`，测试使用内存 adapter。当前采用模块化单体，不引入外部日志平台或分布式追踪服务；以后接 OpenTelemetry 时只需增加 store/export adapter，不改变任务和页面接口。

## 贯通范围

同一 `trace_id` 当前覆盖：

1. HTTP：客户提交 `POST /api/client/jobs`；
2. 任务：排队、运行、重试、超时、取消和完成；
3. 数据：14 类冻结数据集的来源、缓存、备用和缺失状态；
4. Graph：17 个真实节点的开始、结束、跳过、失败和尝试次数；
5. Harness：一致性与偏差输出复核；
6. Model Gateway：客户智能解读的 provider、model、耗时和 Token；
7. 数据库：成功报告归档或写入失败。

数据源快照目前只有最终来源状态，没有每个外部请求的独立起止时间，因此 P5 把它们记录为零时长状态事件，不伪造数据请求耗时。真正可比较的耗时来自 HTTP、任务、Graph 节点、模型调用和数据库写入。

## 指标口径

- 成功率：成功结束的分析数 ÷ 所有已结束分析数；
- P50/P95：按整条 trace 实际耗时排序后采用 nearest-rank；
- 数据源失败率：状态为 `failed` 的数据 span ÷ 全部数据 span；
- 缓存命中率：`cache_hit` 或带 `cache_hit=true` 的数据 span ÷ 全部数据 span；
- 降级率：状态为 `degraded` 的数据 span ÷ 全部数据 span；
- 重试率：尝试次数大于 1 或处于 retrying 的 span ÷ 全部 span；
- Token：模型 span 中 `total_tokens` 的合计。

没有样本时比例和分位数显示 0，不用空值伪装成功。后台“全局慢节点”只使用带真实 `duration_ms` 的步骤。

## 页面结果

客户前台在分析过程中显示每个节点的实际耗时和整次已用时。失败时离开“正在分析”界面，明确展示失败原因、下一步操作和完整追踪号；可重试失败直接提供“只重试失败步骤”。

团队后台 `/admin` 新增深色可靠性工作台，显示成功率、P50/P95、数据源失败率、缓存命中率、Token、最近 trace、单次瀑布和全局慢节点。状态同时使用中文文字和颜色，移动端会转成单列。

HTTP 读取接口为：

- `GET /api/observability/overview`
- `GET /api/observability/traces/{trace_id}`

## 隐私与安全

观测存储只接受短字符串、布尔值和数值元数据。`api_key`、授权头、Cookie、密码、Prompt、系统提示、完整输入输出和原始行情记录会被拒绝；常见 `key=value` 凭据形式还会二次脱敏。模型只记录 provider、model、Token、耗时和状态。

P5 不改变交易边界：真实数据只读，指标和风险仍由确定性代码计算，真实交易保持关闭。

## 验收

离线故障注入不联网、不生成文件：

```powershell
D:\Anaconda\python.exe Scripts\demo_analysis_observability.py
```

专项测试：

```powershell
$env:PYTHONPATH="src"
D:\Anaconda\python.exe -m unittest tests.test_analysis_observability tests.test_analysis_jobs tests.test_dashboard -v
```

演示固定产生一条成功 trace 和一条数据源失败 trace，后者包含两次尝试、缓存降级、Graph 失败、客户操作和追踪号；输出同时证明密钥、Prompt 和完整记录不进入观测存储。
