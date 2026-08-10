import unittest
from datetime import datetime, timezone

from agent_platform.core import (
    AgentResponse,
    GraphEvent,
    GraphExecutionError,
    GraphResult,
    GraphState,
    HarnessExecutionError,
    HarnessResult,
    ModelGatewayResult,
    ModelResponse,
    ModelTraceEvent,
    ModelUsage,
    ObservationAdapter,
    ObservationRecord,
    ObservabilityContractError,
    ObservabilityDashboard,
    TraceEvent,
)


STARTED_AT = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)


class ObservationAdapterTests(unittest.TestCase):
    def test_harness_success_preserves_ordered_call_chain(self):
        execution = HarnessResult(
            response=AgentResponse(content="ok"),
            trace=(
                TraceEvent("preflight.started", "echo"),
                TraceEvent("agent.finished", "echo"),
                TraceEvent("postflight.passed", "echo"),
            ),
        )

        record = ObservationAdapter.from_execution(
            run_id="harness-ok",
            execution=execution,
            started_at=STARTED_AT,
            duration_ms=8,
        )

        self.assertEqual(record.layer, "harness")
        self.assertEqual(record.component, "echo")
        self.assertEqual(record.status, "succeeded")
        self.assertEqual(
            [event.sequence for event in record.events], [1, 2, 3]
        )
        self.assertEqual(record.events[-1].status, "succeeded")

    def test_harness_failure_preserves_original_error(self):
        cause = ValueError("task must not be blank")
        execution = HarnessExecutionError(
            "preflight failed",
            (
                TraceEvent("preflight.started", "echo"),
                TraceEvent("preflight.failed", "echo", str(cause)),
            ),
            cause,
        )

        record = ObservationAdapter.from_execution(
            run_id="harness-failed",
            execution=execution,
            started_at=STARTED_AT,
            duration_ms=3,
        )

        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error_type, "ValueError")
        self.assertEqual(record.error_message, "task must not be blank")
        self.assertEqual(record.events[-1].status, "failed")

    def test_graph_metadata_and_attempts_are_preserved(self):
        execution = GraphResult(
            state=GraphState({"answer": 42}),
            statuses={"planner": "completed"},
            execution_order=("planner",),
            attempts={"planner": 1},
            circuit_breakers={"planner": {"state": "closed"}},
            trace=(
                GraphEvent("graph.started"),
                GraphEvent("node.completed", node="planner", attempt=1),
                GraphEvent("graph.completed"),
            ),
        )

        record = ObservationAdapter.from_execution(
            run_id="graph-ok",
            execution=execution,
            started_at=STARTED_AT,
            duration_ms=21,
            component="research_graph",
        )

        self.assertEqual(record.layer, "graph")
        self.assertEqual(record.component, "research_graph")
        self.assertEqual(record.events[1].component, "planner")
        self.assertEqual(record.events[1].attempt, 1)
        self.assertEqual(record.metadata["execution_order"], ["planner"])

    def test_graph_failure_is_normalized(self):
        cause = TimeoutError("planner timed out")
        execution = GraphExecutionError(
            "graph failed",
            GraphState(),
            {"planner": "failed"},
            ("planner",),
            cause,
            attempts={"planner": 2},
            trace=(GraphEvent("node.timeout", node="planner", attempt=2),),
        )

        record = ObservationAdapter.from_execution(
            run_id="graph-failed",
            execution=execution,
            started_at=STARTED_AT,
            duration_ms=50,
        )

        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error_type, "TimeoutError")
        self.assertEqual(record.metadata["attempts"], {"planner": 2})

    def test_model_usage_and_gateway_latency_are_preserved(self):
        execution = ModelGatewayResult(
            response=ModelResponse(
                content="ok",
                structured_output=None,
                provider="mock",
                model="mock-v1",
                usage=ModelUsage(10, 4, 14),
                latency_ms=125,
                attempts=1,
            ),
            trace=(
                ModelTraceEvent("gateway.started", "mock", "mock-v1"),
                ModelTraceEvent(
                    "gateway.succeeded", "mock", "mock-v1", attempt=1
                ),
            ),
        )

        record = ObservationAdapter.from_execution(
            run_id="model-ok",
            execution=execution,
            started_at=STARTED_AT,
        )

        self.assertEqual(record.layer, "model")
        self.assertEqual(record.component, "mock/mock-v1")
        self.assertEqual(record.duration_ms, 125)
        self.assertEqual(record.total_tokens, 14)


class ObservabilityDashboardTests(unittest.TestCase):
    def test_dashboard_aggregates_failure_rate_tokens_and_latency(self):
        records = (
            self._record("one", "harness", "succeeded", 10),
            self._record("two", "graph", "succeeded", 30),
            self._record(
                "three",
                "model",
                "failed",
                60,
                input_tokens=7,
                output_tokens=3,
                error_message="provider unavailable",
            ),
            self._record("four", "model", "succeeded", 100),
        )

        report = ObservabilityDashboard.build(records)

        self.assertEqual(report.summary["total_runs"], 4)
        self.assertEqual(report.summary["failed_runs"], 1)
        self.assertEqual(report.summary["failure_rate_percent"], 25.0)
        self.assertEqual(report.summary["tokens"]["total"], 10)
        self.assertEqual(report.summary["latency_ms"]["average"], 50.0)
        self.assertEqual(report.summary["latency_ms"]["p95"], 100)
        self.assertEqual(report.by_layer["model"]["failure_rate_percent"], 50.0)

    def test_dashboard_rejects_duplicate_run_ids(self):
        records = (
            self._record("same", "harness", "succeeded", 1),
            self._record("same", "graph", "succeeded", 2),
        )

        with self.assertRaisesRegex(ObservabilityContractError, "unique"):
            ObservabilityDashboard.build(records)

    @staticmethod
    def _record(
        run_id,
        layer,
        status,
        duration_ms,
        *,
        input_tokens=0,
        output_tokens=0,
        error_message="",
    ):
        return ObservationRecord(
            run_id=run_id,
            layer=layer,
            component=f"{layer}_component",
            status=status,
            started_at=STARTED_AT,
            duration_ms=duration_ms,
            events=(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            error_type="RuntimeError" if error_message else "",
            error_message=error_message,
        )


if __name__ == "__main__":
    unittest.main()
