# Graph Engineering 完整能力

## 对外接口

Graph 模块保留两个主要动作：加载 `GraphDefinition`，然后调用 `GraphRunner.run()`。配置解析、拓扑校验、并行波次、重试、超时、熔断、状态合并和 Checkpoint 都由模块内部处理。

```python
registry = NodeRegistry({"prepare": prepare, "finish": finish})
graph = GraphWorkflowLoader(registry).load("workflow.yaml")
result = GraphRunner(graph, checkpoint_store=store).run(initial_state)
```

YAML/JSON 文件只能引用 `NodeRegistry` 中已经允许的处理函数名称，不执行配置里的任意 Python 代码。条件使用 `path + operator + value` 表达式，支持 `eq`、`ne`、`in`、`not_in`、比较、`exists` 和 `truthy`，不使用 `eval`。

## 边 Schema

每条边必须声明两份 Schema：

- `output_schema`：检查 source 节点本次返回的更新字段；
- `input_schema`：在 target 节点执行前检查完整共享状态。

两份 Schema 都使用 Harness SDK 的确定性 JSON Schema 子集。source 输出校验失败时，更新不会进入 GraphState；target 输入校验失败时，target 不会执行。

## 并行和状态合并

`execution.strategy` 可以是 `sequential` 或 `parallel`。并行模式把同一拓扑波次内已经就绪的节点放入线程池，它们读取同一份不可变 `GraphState` 快照。波次结束后按照节点声明顺序合并结果，因此执行记录可复现。

两个并行节点如果写入相同状态键，Graph 会抛出 `GraphMergeConflictError`，不会根据线程完成先后来静默覆盖。某个并行节点失败时，同一波次中成功且无冲突的节点仍会写入 Checkpoint，恢复时不会重复执行。

## 重试、超时和熔断

每个节点可以配置：

```yaml
timeout_seconds: 1
retry:
  max_retries: 2
  delay_seconds: 0.1
circuit_breaker:
  failure_threshold: 3
  reset_timeout_seconds: 60
```

- `max_retries` 表示首次失败后最多额外执行几次；
- `timeout_seconds` 是进程内软超时，超时结果会被拒绝并进入重试；
- 连续失败达到阈值后熔断器进入 `open`，阻止后续调用；
- 冷却时间结束后进入 `half_open` 探测，成功后回到 `closed`；
- 尝试次数和熔断状态写入版本 2 Checkpoint，恢复时继续生效。

Python 线程无法安全强制终止已经开始执行的函数，因此当前超时属于“停止等待并拒绝结果”，不是杀死线程。带不可逆外部副作用的节点应自身支持取消或幂等；进程/容器级强制隔离属于后续工程化增强。

## 可视化

`GraphVisualizer.render_mermaid(graph, result=result)` 返回 Mermaid 文本。没有运行结果时显示静态 DAG；传入 `GraphResult` 后，节点会按 `pending`、`completed`、`skipped`、`failed` 着色。演示默认把文件写到已忽略的 `artifacts/`。

## 运行演示

```powershell
python Scripts\demo_graph_engineering.py
```

演示从 YAML 加载工作流，并展示边 Schema、两个分析节点并行、临时故障重试、熔断状态、版本 2 Checkpoint 和 Mermaid 运行状态图。整个过程使用离线确定性处理函数。
