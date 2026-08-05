# Workflow

存放 Graph/DAG 工作流定义，记录节点、边、输入输出契约、并行策略和恢复策略。

工作流可以通过 Python `GraphDefinition` 或版本 1 YAML/JSON 定义。声明式文件只引用 `NodeRegistry` 中已注册的处理函数，不执行任意配置代码：

```python
graph = GraphDefinition(
    start="prepare",
    nodes={"prepare": prepare, "finish": finish},
    edges=(GraphEdge(
        "prepare",
        "finish",
        output_schema={"type": "object"},
        input_schema={"type": "object"},
    ),),
)
```

完整声明式示例位于 `examples/parallel_analysis.yaml`，包含并行策略、边 Schema、节点重试、超时和熔断。运行入口：

```powershell
python Scripts\demo_graph_engineering.py
```

Graph 支持安全条件表达式、并行拓扑波次、确定性合并、跳过传播、版本 2 Checkpoint 和 Mermaid 状态图。详细语义见 `docs/graph-engineering.md`。
