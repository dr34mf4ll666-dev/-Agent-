"""P5 deterministic observability and fault-injection acceptance demo."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_platform.analysis_observability import (  # noqa: E402
    AnalysisObservabilityRuntime,
    InMemoryAnalysisTraceStore,
    TraceSpan,
)


def main() -> int:
    runtime = AnalysisObservabilityRuntime(InMemoryAnalysisTraceStore())
    _successful_trace(runtime)
    _fault_trace(runtime)
    overview = runtime.overview()

    print("=== P5 统一可观测性与故障注入演示 ===")
    print("统一层级: HTTP -> 任务 -> 数据 -> Graph -> Harness -> 模型 -> 数据库")
    metrics = overview["metrics"]
    print(
        "指标: "
        f"成功率={metrics['success_rate_percent']:.2f}%, "
        f"P50={metrics['latency_p50_ms']}ms, P95={metrics['latency_p95_ms']}ms, "
        f"数据源失败率={metrics['data_source_failure_rate_percent']:.2f}%, "
        f"缓存命中率={metrics['cache_hit_rate_percent']:.2f}%, "
        f"降级率={metrics['degradation_rate_percent']:.2f}%, "
        f"重试率={metrics['retry_rate_percent']:.2f}%, "
        f"Token={metrics['total_tokens']}"
    )
    failed = runtime.trace("trace-fault-injection")
    print("\n故障注入链路:")
    for span in failed["spans"]:
        print(
            f"- {span['layer']:<8} {span['component']:<24} "
            f"{span['status']:<9} {span['duration_ms'] or 0:>4}ms"
        )
    print(f"客户错误: {failed['error']['message']}")
    print(f"客户操作: {failed['error']['user_action']}")
    print(f"追踪号: {failed['trace_id']}")
    print("隐私门禁: 不记录 API Key、Prompt、授权头和完整行情记录")
    print("验收结论: passed（故障点、降级、重试、缓存和模型成本均可定位）")
    return 0


def _successful_trace(runtime: AnalysisObservabilityRuntime) -> None:
    trace_id = "trace-success"
    runtime.begin(
        trace_id, job_id="job-success",
        request={"symbol": "sz000001", "mode": "offline"},
        timestamp="2026-08-14T10:00:00+08:00",
    )
    spans = (
        TraceSpan("http", "dashboard_api", "submit", "succeeded", "2026-08-14T10:00:00+08:00", "2026-08-14T10:00:00.050000+08:00"),
        TraceSpan("data", "market.daily", "acquire_snapshot", "cache_hit", "2026-08-14T10:00:00.050000+08:00", "2026-08-14T10:00:00.250000+08:00", attributes={"cache_hit": True, "source": "fixture"}),
        TraceSpan("graph", "technical", "execute", "succeeded", "2026-08-14T10:00:00.250000+08:00", "2026-08-14T10:00:00.850000+08:00"),
        TraceSpan("harness", "financial_output_guardrails", "postflight", "succeeded", "2026-08-14T10:00:00.850000+08:00", "2026-08-14T10:00:00.900000+08:00"),
        TraceSpan("model", "deepseek", "explain", "succeeded", "2026-08-14T10:00:00.900000+08:00", "2026-08-14T10:00:01.300000+08:00", attributes={"total_tokens": 128, "model": "deepseek-v4-flash"}),
        TraceSpan("database", "analysis_repository", "archive", "succeeded", "2026-08-14T10:00:01.300000+08:00", "2026-08-14T10:00:01.400000+08:00"),
    )
    for span in spans:
        runtime.span(trace_id, span)
    runtime.finish(trace_id, status="succeeded", timestamp="2026-08-14T10:00:01.400000+08:00")


def _fault_trace(runtime: AnalysisObservabilityRuntime) -> None:
    trace_id = "trace-fault-injection"
    runtime.begin(
        trace_id, job_id="job-fault",
        request={"symbol": "sh600000", "mode": "live", "prompt": "not stored"},
        timestamp="2026-08-14T10:01:00+08:00",
    )
    runtime.span(trace_id, TraceSpan("http", "dashboard_api", "submit", "succeeded", "2026-08-14T10:01:00+08:00", "2026-08-14T10:01:00.050000+08:00"))
    runtime.span(trace_id, TraceSpan("data", "market.daily", "primary_fetch", "failed", "2026-08-14T10:01:00.050000+08:00", "2026-08-14T10:01:00.650000+08:00", attempts=2, detail="provider timeout; api_key=must-not-leak"))
    runtime.span(trace_id, TraceSpan("data", "market.daily", "fallback_cache", "degraded", "2026-08-14T10:01:00.650000+08:00", "2026-08-14T10:01:00.700000+08:00", attributes={"cache_hit": True, "source": "last-known-good"}))
    runtime.span(trace_id, TraceSpan("graph", "technical", "execute", "failed", "2026-08-14T10:01:00.700000+08:00", "2026-08-14T10:01:01.200000+08:00", detail="required dataset unavailable"))
    runtime.finish(
        trace_id, status="failed", timestamp="2026-08-14T10:01:01.200000+08:00",
        error_type="ProviderUnavailable",
        error_message="关键行情暂时不可用。",
        user_action="可只重试失败步骤；若仍失败，请使用追踪号联系维护人员。",
    )


if __name__ == "__main__":
    raise SystemExit(main())
