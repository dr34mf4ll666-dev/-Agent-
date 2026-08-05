# 自研 Graph 与 LangGraph 概念映射

本项目保留自研 Graph 引擎，LangGraph 只作为概念参照，不作为隐藏自研缺口的替代实现。

| 本项目 | LangGraph 概念 | 主要差异 |
| --- | --- | --- |
| `GraphDefinition` | `StateGraph` | 本项目同时保存执行策略和节点可靠性策略 |
| `GraphState` | State Schema 的运行值 | 本项目当前使用浅层只读 Mapping，而不是 reducer 注解 |
| `NodeHandler` | Graph node function | 都接收状态并返回状态更新 |
| `GraphEdge` | `add_edge` | 本项目强制边输入输出 Schema |
| 声明式 condition | `add_conditional_edges` | 本项目只允许固定安全操作符，不执行任意表达式 |
| `GraphRunner.run()` | compiled graph 的 `invoke()` | 本项目直接完成拓扑调度、并行波次和确定性合并 |
| `JsonCheckpointStore` | checkpointer | 本项目使用单文件版本化 JSON，保存尝试次数和熔断状态 |
| `GraphVisualizer` | `get_graph().draw_mermaid()` | 都可输出 Mermaid，本项目额外按运行状态着色 |

概念上的最小迁移关系如下：

```python
# 本项目
graph = GraphDefinition(start="prepare", nodes=nodes, edges=edges)
result = GraphRunner(graph).run(initial_state)

# LangGraph 对应思路（示意，不作为项目运行依赖）
builder = StateGraph(StateSchema)
builder.add_node("prepare", prepare)
builder.add_node("finish", finish)
builder.add_edge("prepare", "finish")
builder.set_entry_point("prepare")
result = builder.compile(checkpointer=checkpointer).invoke(initial_state)
```

当前不追求二进制或调用接口兼容。真正可复用的是节点接收状态、返回更新、条件边、Checkpoint 和运行状态这些概念。若以后接入 LangGraph，应编写独立 adapter，把 `GraphDefinition` 转换为 LangGraph builder，而不是让金融 Agent 直接依赖第三方框架。
