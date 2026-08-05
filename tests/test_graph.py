import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core.graph import (
    GraphDefinition,
    GraphEdge,
    GraphExecutionError,
    GraphRunner,
    GraphValidationError,
)
from agent_platform.core.checkpoint import JsonCheckpointStore


EDGE_SCHEMA = {"type": "object"}


def edge(source, target, *, condition=None):
    return GraphEdge(
        source,
        target,
        condition=condition,
        output_schema=EDGE_SCHEMA,
        input_schema=EDGE_SCHEMA,
    )


class GraphRunnerTests(unittest.TestCase):
    def test_graph_executes_nodes_in_dependency_order_and_merges_state(self):
        graph = GraphDefinition(
            start="prepare",
            nodes={
                "prepare": lambda state: {"value": 2},
                "double": lambda state: {"value": state["value"] * 2},
                "finish": lambda state: {"done": True},
            },
            edges=(
                edge("prepare", "double"),
                edge("double", "finish"),
            ),
        )

        result = GraphRunner(graph).run({"request_id": "demo-1"})

        self.assertEqual(result.execution_order, ("prepare", "double", "finish"))
        self.assertEqual(result.state["request_id"], "demo-1")
        self.assertEqual(result.state["value"], 4)
        self.assertTrue(result.state["done"])
        self.assertTrue(all(status == "completed" for status in result.statuses.values()))

    def test_graph_rejects_a_cycle_before_any_node_runs(self):
        calls = []

        def record_a(state):
            calls.append("a")
            return {}

        def record_b(state):
            calls.append("b")
            return {}

        graph = GraphDefinition(
            start="a",
            nodes={"a": record_a, "b": record_b},
            edges=(edge("a", "b"), edge("b", "a")),
        )

        with self.assertRaises(GraphValidationError):
            GraphRunner(graph).run()

        self.assertEqual(calls, [])

    def test_graph_executes_only_the_selected_conditional_branch(self):
        graph = GraphDefinition(
            start="route",
            nodes={
                "route": lambda state: {"route": "approved"},
                "approved": lambda state: {"decision": "approved"},
                "rejected": lambda state: {"decision": "rejected"},
                "rejected_detail": lambda state: {"unexpected": True},
            },
            edges=(
                edge(
                    "route",
                    "approved",
                    condition=lambda state: state["route"] == "approved",
                ),
                edge(
                    "route",
                    "rejected",
                    condition=lambda state: state["route"] == "rejected",
                ),
                edge("rejected", "rejected_detail"),
            ),
        )

        result = GraphRunner(graph).run()

        self.assertEqual(result.execution_order, ("route", "approved"))
        self.assertEqual(result.state["decision"], "approved")
        self.assertEqual(result.statuses["approved"], "completed")
        self.assertEqual(result.statuses["rejected"], "skipped")
        self.assertEqual(result.statuses["rejected_detail"], "skipped")
        self.assertNotIn("unexpected", result.state.values)

    def test_graph_resumes_from_checkpoint_without_rerunning_completed_nodes(self):
        calls = {"prepare": 0, "flaky": 0, "finish": 0}

        def prepare(state):
            calls["prepare"] += 1
            return {"prepared": True}

        def flaky(state):
            calls["flaky"] += 1
            if calls["flaky"] == 1:
                raise RuntimeError("temporary graph failure")
            return {"recovered": True}

        def finish(state):
            calls["finish"] += 1
            return {"done": True}

        graph = GraphDefinition(
            start="prepare",
            nodes={"prepare": prepare, "flaky": flaky, "finish": finish},
            edges=(edge("prepare", "flaky"), edge("flaky", "finish")),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            store = JsonCheckpointStore(Path(temp_dir) / "graph-checkpoint.json")
            runner = GraphRunner(graph, checkpoint_store=store)

            with self.assertRaises(GraphExecutionError) as raised:
                runner.run({"request_id": "resume-1"})

            self.assertEqual(str(raised.exception.cause), "temporary graph failure")
            self.assertEqual(raised.exception.execution_order, ("prepare",))
            self.assertEqual(calls, {"prepare": 1, "flaky": 1, "finish": 0})

            result = runner.run(resume=True)

        self.assertEqual(result.execution_order, ("prepare", "flaky", "finish"))
        self.assertEqual(calls, {"prepare": 1, "flaky": 2, "finish": 1})
        self.assertEqual(result.state["request_id"], "resume-1")
        self.assertTrue(result.state["prepared"])
        self.assertTrue(result.state["recovered"])
        self.assertTrue(result.state["done"])


if __name__ == "__main__":
    unittest.main()
