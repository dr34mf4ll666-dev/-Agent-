from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_platform.analysis_observability import (
    AnalysisObservabilityRuntime,
    InMemoryAnalysisTraceStore,
    JsonAnalysisTraceStore,
    TraceSpan,
)


T0 = "2026-08-14T10:00:00+08:00"
T1 = "2026-08-14T10:00:01+08:00"
T2 = "2026-08-14T10:00:03+08:00"


def _runtime() -> AnalysisObservabilityRuntime:
    return AnalysisObservabilityRuntime(InMemoryAnalysisTraceStore())


class AnalysisObservabilityTests(unittest.TestCase):
    def test_trace_builds_waterfall_and_updates_running_span(self) -> None:
        runtime = _runtime()
        runtime.begin("trace-1", job_id="job-1", request={"symbol": "sz000001", "mode": "live"}, timestamp=T0)
        runtime.span("trace-1", TraceSpan("graph", "technical", "execute", "running", T0))
        runtime.span("trace-1", TraceSpan("graph", "technical", "execute", "succeeded", T0, T1, attempts=1))
        runtime.span("trace-1", TraceSpan("database", "analysis_repository", "archive", "succeeded", T1, T2))
        runtime.finish("trace-1", status="succeeded", timestamp=T2)

        trace = runtime.trace("trace-1")
        self.assertEqual(trace["status"], "succeeded")
        self.assertEqual(trace["duration_ms"], 3000)
        self.assertEqual(len(trace["spans"]), 2)
        self.assertEqual(trace["spans"][0]["duration_ms"], 1000)
        self.assertEqual(trace["spans"][1]["duration_ms"], 2000)

    def test_overview_calculates_required_metrics(self) -> None:
        runtime = _runtime()
        runtime.begin("trace-ok", job_id="job-ok", request={}, timestamp=T0)
        runtime.span("trace-ok", TraceSpan("data", "market.daily", "fetch", "cache_hit", T0, T1, attributes={"cache_hit": True}))
        runtime.span("trace-ok", TraceSpan("model", "deepseek", "explain", "succeeded", T1, T2, attributes={"total_tokens": 128}))
        runtime.finish("trace-ok", status="succeeded", timestamp=T2)
        runtime.begin("trace-bad", job_id="job-bad", request={}, timestamp=T0)
        runtime.span("trace-bad", TraceSpan("data", "finance.indicator", "fetch", "failed", T0, T1, attempts=2))
        runtime.finish("trace-bad", status="failed", timestamp=T1, error_type="ProviderError", error_message="down", user_action="retry")

        metrics = runtime.overview()["metrics"]
        self.assertEqual(metrics["success_rate_percent"], 50.0)
        self.assertEqual(metrics["latency_p50_ms"], 1000)
        self.assertEqual(metrics["latency_p95_ms"], 3000)
        self.assertEqual(metrics["data_source_failure_rate_percent"], 50.0)
        self.assertEqual(metrics["cache_hit_rate_percent"], 50.0)
        self.assertEqual(metrics["retry_rate_percent"], 33.33)
        self.assertEqual(metrics["total_tokens"], 128)

    def test_trace_redacts_secrets_and_refuses_raw_payload_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.json"
            runtime = AnalysisObservabilityRuntime(JsonAnalysisTraceStore(path))
            runtime.begin("trace-secret", job_id="job-secret", request={"symbol": "sz000001", "prompt": "private"}, timestamp=T0)
            runtime.span(
                "trace-secret",
                TraceSpan(
                    "model", "deepseek", "explain", "failed", T0, T1,
                    detail="api_key=sk-sensitive token=private-token",
                    attributes={"prompt": "full prompt", "api_key": "sk-sensitive", "model": "deepseek-v4-flash"},
                ),
            )
            runtime.finish("trace-secret", status="failed", timestamp=T1, error_type="AuthError", error_message="authorization: Bearer-secret", user_action="check configuration")

            raw = path.read_text(encoding="utf-8")
            trace = runtime.trace("trace-secret")
            self.assertNotIn("sk-sensitive", raw)
            self.assertNotIn("private-token", raw)
            self.assertNotIn("full prompt", raw)
            self.assertEqual(trace["request"], {"symbol": "sz000001"})
            self.assertEqual(trace["spans"][0]["attributes"], {"model": "deepseek-v4-flash"})

    def test_json_store_survives_runtime_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.json"
            first = AnalysisObservabilityRuntime(JsonAnalysisTraceStore(path))
            first.begin("trace-1", job_id="job-1", request={"mode": "offline"}, timestamp=T0)
            first.finish("trace-1", status="cancelled", timestamp=T1, user_action="start again")
            second = AnalysisObservabilityRuntime(JsonAnalysisTraceStore(path))
            self.assertEqual(second.trace("trace-1")["status"], "cancelled")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 1)

    def test_restart_never_rewinds_a_finished_trace_to_creation_time(self) -> None:
        store = InMemoryAnalysisTraceStore()
        first = AnalysisObservabilityRuntime(store)
        first.begin("trace-1", job_id="job-1", request={}, timestamp=T0)
        first.span("trace-1", TraceSpan("task", "analysis", "run", "succeeded", T0, T2))
        first.finish("trace-1", status="succeeded", timestamp=T2)

        second = AnalysisObservabilityRuntime(store)
        second.begin("trace-1", job_id="job-1", request={}, timestamp=T0)

        self.assertEqual(second.trace("trace-1")["duration_ms"], 3000)

    def test_all_required_layers_are_supported(self) -> None:
        for layer in ("http", "task", "data", "graph", "harness", "model", "database"):
            with self.subTest(layer=layer):
                runtime = _runtime()
                runtime.begin("trace", job_id="job", request={}, timestamp=T0)
                runtime.span("trace", TraceSpan(layer, "component", "operation", "succeeded", T0, T1))
                self.assertEqual(runtime.trace("trace")["spans"][0]["layer"], layer)


if __name__ == "__main__":
    unittest.main()
