# Workflow

存放 Graph/DAG 工作流定义，记录节点、边、输入输出契约、并行策略和恢复策略。

第三周已经在 `src/agent_platform/core/graph.py` 建立最小非金融 Graph 运行时。当前工作流通过 Python 的 `GraphDefinition` 定义，因为节点处理函数本身需要绑定可执行代码：

```python
graph = GraphDefinition(
    start="prepare",
    nodes={"prepare": prepare, "finish": finish},
    edges=(GraphEdge("prepare", "finish"),),
)
```

当前边界：顺序执行 DAG，支持条件边和 JSON Checkpoint；暂不提供 YAML/JSON 解析、真正的并行调度或金融业务工作流。
