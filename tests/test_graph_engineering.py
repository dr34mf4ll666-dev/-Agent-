import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (
    CircuitBreakerPolicy,
    GraphCircuitOpenError,
    GraphCheckpointError,
    GraphDefinition,
    GraphEdge,
    GraphExecutionError,
    GraphExecutionPolicy,
    GraphMergeConflictError,
    GraphNodeTimeoutError,
    GraphRunner,
    GraphSchemaError,
    GraphVisualizer,
    GraphWorkflowLoader,
    JsonCheckpointStore,
    NodeExecutionPolicy,
    NodeRegistry,
    WorkflowConfigurationError,
)


ANY_OBJECT_SCHEMA = {"type": "object"}


def edge(source, target, **kwargs):
    return GraphEdge(
        source,
        target,
        output_schema=ANY_OBJECT_SCHEMA,
        input_schema=ANY_OBJECT_SCHEMA,
        **kwargs,
    )


class GraphEngineeringTests(unittest.TestCase):
    def test_rejects_corrupted_version_two_checkpoint(self):
        payload = {
            "version": 2,
            "graph_signature": "demo",
            "state": {},
            "statuses": {"start": "failed"},
            "edge_decisions": {},
            "execution_order": [],
            "attempts": {"start": 1},
            "circuit_breakers": {
                "start": {"state": "broken", "failures": 1, "opened_at": None}
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "corrupted.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(GraphCheckpointError):
                JsonCheckpointStore(path).load()

    def test_loads_json_and_yaml_workflows_with_safe_conditions(self):
        handlers = NodeRegistry(
            {
                "route_handler": lambda state: {"route": "approved"},
                "approved_handler": lambda state: {"decision": "approved"},
                "rejected_handler": lambda state: {"decision": "rejected"},
            }
        )
        loader = GraphWorkflowLoader(handlers)
        workflow = {
            "version": 1,
            "start": "route",
            "execution": {"strategy": "parallel", "max_workers": 2},
            "nodes": {
                "route": {"handler": "route_handler"},
                "approved": {"handler": "approved_handler"},
                "rejected": {"handler": "rejected_handler"},
            },
            "edges": [
                {
                    "source": "route",
                    "target": "approved",
                    "condition": {
                        "path": "route",
                        "operator": "eq",
                        "value": "approved",
                    },
                    "output_schema": ANY_OBJECT_SCHEMA,
                    "input_schema": ANY_OBJECT_SCHEMA,
                },
                {
                    "source": "route",
                    "target": "rejected",
                    "condition": {
                        "path": "route",
                        "operator": "eq",
                        "value": "rejected",
                    },
                    "output_schema": ANY_OBJECT_SCHEMA,
                    "input_schema": ANY_OBJECT_SCHEMA,
                },
            ],
        }
        yaml_text = """\
version: 1
start: route
execution:
  strategy: parallel
  max_workers: 2
nodes:
  route:
    handler: route_handler
  approved:
    handler: approved_handler
  rejected:
    handler: rejected_handler
edges:
  - source: route
    target: approved
    condition: {path: route, operator: eq, value: approved}
    output_schema: {type: object}
    input_schema: {type: object}
  - source: route
    target: rejected
    condition: {path: route, operator: eq, value: rejected}
    output_schema: {type: object}
    input_schema: {type: object}
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "workflow.json"
            yaml_path = Path(temp_dir) / "workflow.yaml"
            json_path.write_text(json.dumps(workflow), encoding="utf-8")
            yaml_path.write_text(yaml_text, encoding="utf-8")

            json_graph = loader.load(json_path)
            yaml_graph = loader.load(yaml_path)

        for graph in (json_graph, yaml_graph):
            result = GraphRunner(graph).run()
            self.assertEqual(result.execution_order, ("route", "approved"))
            self.assertEqual(result.statuses["rejected"], "skipped")
            self.assertEqual(result.state["decision"], "approved")
            self.assertEqual(graph.execution.strategy, "parallel")

    def test_workflow_rejects_unknown_handler_and_missing_edge_schema(self):
        loader = GraphWorkflowLoader(NodeRegistry({"known": lambda state: {}}))
        unknown_handler = {
            "version": 1,
            "start": "start",
            "nodes": {"start": {"handler": "missing"}},
            "edges": [],
        }
        with self.assertRaises(WorkflowConfigurationError):
            loader.from_mapping(unknown_handler)

        missing_schema = {
            "version": 1,
            "start": "start",
            "nodes": {
                "start": {"handler": "known"},
                "finish": {"handler": "known"},
            },
            "edges": [{"source": "start", "target": "finish"}],
        }
        with self.assertRaises(WorkflowConfigurationError):
            loader.from_mapping(missing_schema)

    def test_validates_edge_output_and_target_input_schema(self):
        output_graph = GraphDefinition(
            start="source",
            nodes={"source": lambda state: {"score": "bad"}, "target": lambda state: {}},
            edges=(
                GraphEdge(
                    "source",
                    "target",
                    output_schema={
                        "type": "object",
                        "required": ["score"],
                        "properties": {"score": {"type": "number"}},
                    },
                    input_schema=ANY_OBJECT_SCHEMA,
                ),
            ),
        )
        with self.assertRaises(GraphExecutionError) as output_error:
            GraphRunner(output_graph).run()
        self.assertIsInstance(output_error.exception.cause, GraphSchemaError)
        self.assertEqual(output_error.exception.statuses["source"], "failed")

        input_graph = GraphDefinition(
            start="source",
            nodes={"source": lambda state: {"score": 1}, "target": lambda state: {}},
            edges=(
                GraphEdge(
                    "source",
                    "target",
                    output_schema=ANY_OBJECT_SCHEMA,
                    input_schema={
                        "type": "object",
                        "required": ["approved"],
                        "properties": {"approved": {"type": "boolean"}},
                    },
                ),
            ),
        )
        with self.assertRaises(GraphExecutionError) as input_error:
            GraphRunner(input_graph).run()
        self.assertIsInstance(input_error.exception.cause, GraphSchemaError)
        self.assertEqual(input_error.exception.statuses["source"], "completed")
        self.assertEqual(input_error.exception.statuses["target"], "failed")

    def test_runs_independent_nodes_in_parallel_and_merges_deterministically(self):
        barrier = threading.Barrier(2)

        def left(state):
            barrier.wait(timeout=1)
            return {"left": 1}

        def right(state):
            barrier.wait(timeout=1)
            return {"right": 2}

        graph = GraphDefinition(
            start="start",
            nodes={
                "start": lambda state: {"ready": True},
                "left": left,
                "right": right,
                "join": lambda state: {"total": state["left"] + state["right"]},
            },
            edges=(
                edge("start", "left"),
                edge("start", "right"),
                edge("left", "join"),
                edge("right", "join"),
            ),
            execution=GraphExecutionPolicy(strategy="parallel", max_workers=2),
        )

        result = GraphRunner(graph).run()

        self.assertEqual(result.execution_order, ("start", "left", "right", "join"))
        self.assertEqual(result.state["total"], 3)
        wave_events = [event for event in result.trace if event.event == "graph.wave.started"]
        self.assertTrue(any("left,right" in event.detail for event in wave_events))

    def test_parallel_nodes_cannot_silently_overwrite_same_state_key(self):
        graph = GraphDefinition(
            start="start",
            nodes={
                "start": lambda state: {},
                "left": lambda state: {"shared": "left"},
                "right": lambda state: {"shared": "right"},
            },
            edges=(edge("start", "left"), edge("start", "right")),
            execution=GraphExecutionPolicy(strategy="parallel", max_workers=2),
        )

        with self.assertRaises(GraphExecutionError) as raised:
            GraphRunner(graph).run()

        self.assertIsInstance(raised.exception.cause, GraphMergeConflictError)
        self.assertNotIn("shared", raised.exception.state.values)

    def test_retries_node_and_records_attempts(self):
        calls = {"flaky": 0}

        def flaky(state):
            calls["flaky"] += 1
            if calls["flaky"] < 3:
                raise RuntimeError("temporary")
            return {"done": True}

        graph = GraphDefinition(
            start="flaky",
            nodes={"flaky": flaky},
            node_policies={
                "flaky": NodeExecutionPolicy(max_retries=2),
            },
        )

        result = GraphRunner(graph).run()

        self.assertEqual(result.attempts["flaky"], 3)
        self.assertTrue(result.state["done"])
        self.assertEqual(
            len([event for event in result.trace if event.event == "graph.node.retry"]),
            2,
        )

    def test_timeout_retries_then_fails_with_trace(self):
        def slow(state):
            time.sleep(0.05)
            return {"late": True}

        graph = GraphDefinition(
            start="slow",
            nodes={"slow": slow},
            node_policies={
                "slow": NodeExecutionPolicy(
                    max_retries=1,
                    timeout_seconds=0.005,
                )
            },
        )

        with self.assertRaises(GraphExecutionError) as raised:
            GraphRunner(graph).run()

        self.assertIsInstance(raised.exception.cause, GraphNodeTimeoutError)
        self.assertEqual(raised.exception.attempts["slow"], 2)
        timeout_events = [
            event for event in raised.exception.trace
            if event.event == "graph.node.timeout"
        ]
        self.assertEqual(len(timeout_events), 2)

    def test_circuit_breaker_state_is_checkpointed_and_half_opens_on_resume(self):
        calls = {"unstable": 0}

        def unstable(state):
            calls["unstable"] += 1
            if calls["unstable"] == 1:
                raise RuntimeError("first call fails")
            return {"recovered": True}

        graph = GraphDefinition(
            start="unstable",
            nodes={"unstable": unstable},
            node_policies={
                "unstable": NodeExecutionPolicy(
                    max_retries=3,
                    circuit_breaker=CircuitBreakerPolicy(
                        failure_threshold=1,
                        reset_timeout_seconds=0,
                    ),
                )
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / "graph.json"
            store = JsonCheckpointStore(checkpoint_path)
            runner = GraphRunner(graph, checkpoint_store=store)

            with self.assertRaises(GraphExecutionError) as first_run:
                runner.run()
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            result = runner.run(resume=True)

        self.assertIsInstance(first_run.exception.cause, GraphCircuitOpenError)
        self.assertEqual(checkpoint["circuit_breakers"]["unstable"]["state"], "open")
        self.assertEqual(result.attempts["unstable"], 2)
        self.assertEqual(result.circuit_breakers["unstable"]["state"], "closed")
        self.assertTrue(result.state["recovered"])

    def test_mermaid_visualization_contains_structure_and_runtime_status(self):
        graph = GraphDefinition(
            start="start",
            nodes={"start": lambda state: {"ok": True}, "finish": lambda state: {}},
            edges=(
                edge(
                    "start",
                    "finish",
                    condition=lambda state: state["ok"],
                    condition_label="ok is true",
                ),
            ),
        )
        result = GraphRunner(graph).run()

        mermaid = GraphVisualizer().render_mermaid(graph, result=result)

        self.assertIn("flowchart TD", mermaid)
        self.assertIn('start["start"]', mermaid)
        self.assertIn('finish["finish"]', mermaid)
        self.assertIn("ok is true", mermaid)
        self.assertIn("class start,finish completed", mermaid)


if __name__ == "__main__":
    unittest.main()
