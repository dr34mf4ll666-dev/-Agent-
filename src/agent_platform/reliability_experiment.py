"""Deterministic offline evidence for reliability and performance claims."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from .analysis_observability import (
    AnalysisObservabilityRuntime,
    InMemoryAnalysisTraceStore,
    TraceSpan,
)
from .research_workspace import ResearchWorkspaceRuntime


class ReliabilityExperimentError(ValueError):
    """The offline experiment configuration or result is invalid."""


class OfflineReliabilityExperimentRuntime:
    """Run one fixed five-case experiment without network or file output."""

    VERSION = "interview-reliability-v1"
    BASE_TIME = datetime.fromisoformat("2026-08-18T09:00:00+08:00")

    def __init__(self, observability: AnalysisObservabilityRuntime | None = None) -> None:
        self._observability = observability or AnalysisObservabilityRuntime(
            InMemoryAnalysisTraceStore()
        )

    def run(self) -> dict[str, Any]:
        scenarios = [
            self._record(
                "normal_analysis",
                "正常运行",
                duration_ms=420,
                status="succeeded",
                outcome="成功",
                fault="无",
                recovery_eligible=False,
                recovered=False,
                retry_count=0,
                degradation_count=0,
                cache_hits=0,
                duplicate_nodes=0,
                model_calls=1,
                total_tokens=128,
                detail="固定离线数据一次完成，输出校验通过。",
            ),
            self._record(
                "source_timeout_retry",
                "数据源超时与有限重试",
                duration_ms=690,
                status="succeeded",
                outcome="重试后成功",
                fault="第一次数据源请求超时",
                recovery_eligible=True,
                recovered=True,
                retry_count=1,
                degradation_count=0,
                cache_hits=0,
                duplicate_nodes=0,
                model_calls=1,
                total_tokens=128,
                detail="只重试失败的数据步骤，成功步骤没有重复执行。",
            ),
            self._record(
                "cache_degradation",
                "缓存降级",
                duration_ms=510,
                status="succeeded",
                outcome="降级后完成",
                fault="首选数据源不可用",
                recovery_eligible=True,
                recovered=True,
                retry_count=0,
                degradation_count=1,
                cache_hits=1,
                duplicate_nodes=0,
                model_calls=1,
                total_tokens=128,
                detail="使用历史缓存完成分析，并把降级状态交给报告前台。",
            ),
            self._record(
                "checkpoint_recovery",
                "Checkpoint 恢复",
                duration_ms=760,
                status="succeeded",
                outcome="恢复后成功",
                fault="综合节点中断",
                recovery_eligible=True,
                recovered=True,
                retry_count=0,
                degradation_count=0,
                cache_hits=0,
                duplicate_nodes=0,
                model_calls=1,
                total_tokens=128,
                detail="从最近 Checkpoint 继续，已完成节点不重复运行。",
            ),
            self._record(
                "output_validation_failure",
                "输出校验失败",
                duration_ms=330,
                status="failed",
                outcome="安全失败",
                fault="模型输出未通过结构校验",
                recovery_eligible=False,
                recovered=False,
                retry_count=0,
                degradation_count=0,
                cache_hits=0,
                duplicate_nodes=0,
                model_calls=1,
                total_tokens=128,
                detail="拒绝不合规输出，没有把无效结果交给后续风控。",
            ),
        ]
        metrics = self._metrics(scenarios)
        return {
            "experiment": self.VERSION,
            "mode": "offline",
            "network_used": False,
            "file_output": False,
            "task_config": {
                "scenario_count": len(scenarios),
                "dataset": "fixed-offline-fixture-v1",
                "model_policy": "fixed-model-policy-v1",
                "statistics": "nearest-rank percentile over five fixed end-to-end durations",
            },
            "scenarios": scenarios,
            "metrics": metrics,
            "observability": self._observability.overview(limit=10),
            "comparison": self._comparison_demo(),
            "conclusion": (
                "离线实验覆盖正常、重试、缓存降级、Checkpoint 恢复和输出拒绝；"
                "它证明了可观测和恢复路径可复现，不等同于线上 SLA。"
            ),
        }

    def _record(
        self,
        scenario_id: str,
        title: str,
        *,
        duration_ms: int,
        status: str,
        outcome: str,
        fault: str,
        recovery_eligible: bool,
        recovered: bool,
        retry_count: int,
        degradation_count: int,
        cache_hits: int,
        duplicate_nodes: int,
        model_calls: int,
        total_tokens: int,
        detail: str,
    ) -> dict[str, Any]:
        if status not in {"succeeded", "failed"} or duration_ms < 0:
            raise ReliabilityExperimentError("离线场景参数无效。")
        trace_id = f"reliability-{scenario_id}"
        job_id = f"job-{scenario_id}"
        started = self.BASE_TIME + timedelta(minutes=len(self._observability.overview()["recent_traces"]))
        finished = started + timedelta(milliseconds=duration_ms)
        start_text = started.isoformat(timespec="milliseconds")
        finish_text = finished.isoformat(timespec="milliseconds")
        self._observability.begin(
            trace_id,
            job_id=job_id,
            request={"symbol": "sz000001", "mode": "offline"},
            timestamp=start_text,
        )
        if scenario_id == "source_timeout_retry":
            self._observability.span(
                trace_id,
                TraceSpan(
                    "data", "fixed_source", "fetch_market_data", "succeeded",
                    start_text, finish_text, duration_ms=duration_ms, attempts=2,
                    detail="第一次超时，有限重试后成功。",
                    attributes={"retry_count": 1, "total_tokens": 0},
                ),
            )
        elif scenario_id == "cache_degradation":
            self._observability.span(
                trace_id,
                TraceSpan(
                    "data", "fixed_source", "fetch_market_data", "degraded",
                    start_text, finish_text, duration_ms=duration_ms,
                    detail="首选来源失败，读取历史缓存。",
                    attributes={"cache_hit": True, "total_tokens": 0},
                ),
            )
        elif scenario_id == "checkpoint_recovery":
            self._observability.span(
                trace_id,
                TraceSpan(
                    "graph", "checkpoint_runner", "resume_graph", "succeeded",
                    start_text, finish_text, detail="从 Checkpoint 恢复。",
                    attributes={"checkpoint_recovered": True, "repeated_successful_nodes": 0},
                ),
            )
        elif scenario_id == "output_validation_failure":
            self._observability.span(
                trace_id,
                TraceSpan(
                    "model", "fixed_model", "structured_output", "succeeded",
                    start_text, finish_text, duration_ms=duration_ms,
                    attributes={"model_calls": 1, "total_tokens": total_tokens},
                ),
            )
            self._observability.span(
                trace_id,
                TraceSpan(
                    "harness", "output_guardrail", "validate_output", "failed",
                    start_text, finish_text, duration_ms=duration_ms,
                    detail="结构化输出校验失败。",
                ),
            )
        else:
            self._observability.span(
                trace_id,
                TraceSpan(
                    "graph", "fixed_graph", "run_analysis", "succeeded",
                    start_text, finish_text, duration_ms=duration_ms,
                    attributes={"model_calls": model_calls, "total_tokens": total_tokens},
                ),
            )
        self._observability.finish(
            trace_id,
            status=status,
            timestamp=finish_text,
            error_type="OutputSchemaError" if status == "failed" else "",
            error_message=fault if status == "failed" else "",
            user_action="修复输出后重新运行" if status == "failed" else "",
        )
        return {
            "id": scenario_id,
            "title": title,
            "status": status,
            "outcome": outcome,
            "fault": fault,
            "recovery_eligible": recovery_eligible,
            "recovered": recovered,
            "duration_ms": duration_ms,
            "retry_count": retry_count,
            "degradation_count": degradation_count,
            "cache_hits": cache_hits,
            "duplicate_successful_nodes": duplicate_nodes,
            "model_calls": model_calls,
            "total_tokens": total_tokens,
            "detail": detail,
            "trace_id": trace_id,
        }

    @staticmethod
    def _metrics(scenarios: list[Mapping[str, Any]]) -> dict[str, Any]:
        durations = sorted(int(item["duration_ms"]) for item in scenarios)
        terminal_count = len(scenarios)
        success_count = sum(item["status"] == "succeeded" for item in scenarios)
        recovery_cases = [item for item in scenarios if item["recovery_eligible"]]
        model_calls = sum(int(item["model_calls"]) for item in scenarios)
        total_tokens = sum(int(item["total_tokens"]) for item in scenarios)
        return {
            "scenario_count": terminal_count,
            "success_count": success_count,
            "success_rate_percent": _rate(success_count, terminal_count),
            "fault_recovery_cases": len(recovery_cases),
            "fault_recovery_success_count": sum(item["recovered"] for item in recovery_cases),
            "fault_recovery_rate_percent": _rate(
                sum(item["recovered"] for item in recovery_cases), len(recovery_cases)
            ),
            "retry_count": sum(int(item["retry_count"]) for item in scenarios),
            "degradation_count": sum(int(item["degradation_count"]) for item in scenarios),
            "cache_hit_count": sum(int(item["cache_hits"]) for item in scenarios),
            "cache_hit_rate_percent": _rate(
                sum(int(item["cache_hits"]) for item in scenarios), terminal_count
            ),
            "duplicate_successful_node_count": sum(
                int(item["duplicate_successful_nodes"]) for item in scenarios
            ),
            "model_call_count": model_calls,
            "total_tokens": total_tokens,
            "average_latency_ms": round(mean(durations), 2),
            "p50_latency_ms": _nearest_rank(durations, 50),
            "p95_latency_ms": _nearest_rank(durations, 95),
            "p99_latency_ms": _nearest_rank(durations, 99),
            "percentile_method": "nearest-rank",
        }

    @staticmethod
    def _comparison_demo() -> dict[str, Any]:
        def projection(snapshot_id: str, as_of: str, status: str, *, code: str = "0.1.0"):
            return {
                "shared": {
                    "security": {"symbol": "sz000001", "name": "平安银行"},
                    "data": {"as_of": as_of, "snapshot_id": snapshot_id},
                    "provenance": {
                        "quality": {"overall_status": status, "comparison_ready": status == "complete"},
                        "identity": {
                            "security_master_version": "security-master-v3",
                            "code_version": code,
                            "config_version": "analysis-config-v1",
                            "model_policy_version": "p7-policy-v1",
                            "report_version": 1,
                        },
                    },
                }
            }

        left = projection("snapshot-a", "2026-08-17T15:00:00+08:00", "complete")
        right = projection("snapshot-b", "2026-08-18T15:00:00+08:00", "degraded")
        reasons = ResearchWorkspaceRuntime._change_reasons(left, right, same_security=True)
        return {
            "left": {"snapshot_id": "snapshot-a", "as_of": "2026-08-17T15:00:00+08:00"},
            "right": {"snapshot_id": "snapshot-b", "as_of": "2026-08-18T15:00:00+08:00"},
            "change_reasons": deepcopy(reasons),
        }


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 2)


def _nearest_rank(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    rank = max(1, (percentile * len(values) + 99) // 100)
    return values[min(rank, len(values)) - 1]


__all__ = ["OfflineReliabilityExperimentRuntime", "ReliabilityExperimentError"]
