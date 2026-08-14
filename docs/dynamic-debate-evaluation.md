# 受约束动态多空辩论固定评测

## 1. 这项补强解决什么问题

客户页面已经能让 DeepSeek 根据四个 Agent 的真实证据生成动态多空观点，但“能调用”不等于“质量已经量化”。本评测固定研究底稿、轮数和重复次数，把原模板辩论作为基线，观察动态模式是否守住证据与安全边界，以及语言是否真的更有变化。

`DynamicDebateEvaluationRuntime.run(dataset)` 是统一接口。它内部完成固定数据加载、四 Agent 离线底稿生成、模板与动态辩论重复执行、确定性复核和统计。命令行、后台按钮和自动化测试都复用这个接口。

## 2. 固定评测集

默认数据在 `src/agent_platform/resources/dynamic_debate_evaluation.json`：

- `two-round-repeatability`：两轮辩论，重复两次；
- `three-round-countering`：三轮辩论，重复两次；
- 共 2 个用例、4 次完整运行；
- 每次使用同一份四 Agent 离线研究底稿，避免外部行情变化干扰语言评测。

## 3. 指标口径

| 指标 | 含义 |
| --- | --- |
| 候选证据有效率 | 被后端接受的模型候选数 ÷ 所有语义候选尝试数 |
| 最终证据有效率 | 通过确定性证据重放校验的最终报告数 ÷ 总运行数 |
| 观点多样性 | 不重复的 Claim/Reasoning 组合数 ÷ 总运行数 |
| 正反平衡率 | Bull 与 Bear 都覆盖至少两个 Specialist 的运行数 ÷ 总运行数 |
| 重试率 | 发生第二次语义尝试的运行数 ÷ 总运行数 |
| 降级率 | 最终退回固定模板的运行数 ÷ 总运行数 |
| 平均耗时与 Token | 动态模式四次运行的实际模型调用统计 |
| 结果稳定性 | 同一用例重复运行时，最终有效性、平衡性和安全字段保持一致的比例 |

逐次原始结果同时显示用例、重复序号、轮数、模式、尝试次数、有效性、平衡性、Token 和耗时，结论不是一句“效果更好”。

默认通过阈值也写在评测集里：候选证据有效率不低于 75%，最终证据有效率与正反平衡率为 100%，重试率不高于 50%，降级率不高于 25%，结果稳定性为 100%，且动态观点多样性必须高于模板基线。即使固定模板成功兜底，只要真实模型频繁失败或全部降级，真实评测也不会显示为通过。

## 4. 怎么运行

离线模式不会联网，用脚本化 Mock 故意制造一次未知证据，验证拒绝和重试链路：

```powershell
D:\Anaconda\python.exe Scripts\demo_dynamic_debate_evaluation.py
```

真实模式会隐藏询问 DeepSeek Key，然后用相同评测集发起受控调用：

```powershell
D:\Anaconda\python.exe Scripts\demo_dynamic_debate_evaluation.py --live
```

真实评测默认覆盖保存唯一固定文件 `.runtime/llm-evaluation/deepseek-fixed-v1.json`，避免每次运行堆积文件；如果需要另存一份，再显式指定其他路径。JSON 会包含每次最终辩论、trace 和统计：

```powershell
D:\Anaconda\python.exe Scripts\demo_dynamic_debate_evaluation.py --live --output .runtime\llm-evaluation\custom.json
```

安装项目后可把脚本名替换为 `agent-platform debate-eval`。Web 后台 `/admin` 的 C 阶段也有“C1+ · 动态辩论量化评测”卡片。

## 5. 安全与结论边界

- LLM 只能生成 Claim、Evidence ID 和 Reasoning 候选；真实证据由后端重新填入并复算。
- 动态辩论不修改 C1 Synthesis、价格区间、Trader 候选、仓位或 Risk Manager 结论。
- `simulation_only=true`、`order_created=false`、`real_trading_allowed=false` 不变。
- Mock 结果只证明评测程序、拒绝、重试、降级和统计方法可以复现，不能证明 DeepSeek 线上质量。
- 真实结果只代表本次 Key 对应的模型、固定评测集与运行时网络状态；更换模型或提示后应重新运行并保存结果。
