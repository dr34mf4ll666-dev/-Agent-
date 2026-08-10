"""T4.2 第一阶段：统一可观测面板离线验收演示。"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (  # noqa: E402
    AgentHarness,
    AgentRequest,
    EchoAgent,
    GraphDefinition,
    GraphEdge,
    GraphRunner,
    HarnessExecutionError,
    MockModelAdapter,
    ModelGateway,
    ModelRequest,
    ModelUsage,
    ObservationAdapter,
    ObservabilityDashboard,
)


BEIJING = ZoneInfo("Asia/Shanghai")
STATUS_ZH = {
    "succeeded": "成功",
    "failed": "失败",
    "started": "开始",
    "retrying": "重试中",
    "skipped": "跳过",
    "info": "信息",
}
LAYER_ZH = {"harness": "执行框架", "graph": "流程图", "model": "大模型"}


class SequenceClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def _run_harness(task: str):
    started_at = datetime.now(BEIJING)
    started = time.perf_counter()
    try:
        execution = AgentHarness(EchoAgent()).run(AgentRequest(task=task))
    except HarnessExecutionError as error:
        execution = error
    duration_ms = max(1, round((time.perf_counter() - started) * 1000))
    return execution, started_at, duration_ms


def _run_graph():
    schema = {"type": "object"}
    graph = GraphDefinition(
        start="planner",
        nodes={
            "planner": lambda state: {"plan": "observe"},
            "worker": lambda state: {"result": f"{state['plan']}_done"},
        },
        edges=(
            GraphEdge(
                "planner",
                "worker",
                output_schema=schema,
                input_schema=schema,
            ),
        ),
    )
    started_at = datetime.now(BEIJING)
    started = time.perf_counter()
    execution = GraphRunner(graph).run({"request_id": "obs-graph-001"})
    duration_ms = max(1, round((time.perf_counter() - started) * 1000))
    return execution, started_at, duration_ms


def _run_model():
    gateway = ModelGateway(
        MockModelAdapter(
            content="离线模型链路正常",
            usage=ModelUsage(input_tokens=12, output_tokens=9, total_tokens=21),
        ),
        clock=SequenceClock(10.0, 10.125),
    )
    started_at = datetime.now(BEIJING)
    return gateway.generate(ModelRequest(prompt="检查可观测链路")), started_at


def build_report():
    harness_ok, started_at, duration_ms = _run_harness("观察一次正常执行")
    harness_failed, failed_at, failed_duration_ms = _run_harness("   ")
    graph_result, graph_at, graph_duration_ms = _run_graph()
    model_result, model_at = _run_model()

    records = (
        ObservationAdapter.from_execution(
            run_id="harness-success",
            execution=harness_ok,
            started_at=started_at,
            duration_ms=duration_ms,
        ),
        ObservationAdapter.from_execution(
            run_id="harness-expected-failure",
            execution=harness_failed,
            started_at=failed_at,
            duration_ms=failed_duration_ms,
        ),
        ObservationAdapter.from_execution(
            run_id="graph-success",
            execution=graph_result,
            started_at=graph_at,
            duration_ms=graph_duration_ms,
            component="observability_demo_graph",
        ),
        ObservationAdapter.from_execution(
            run_id="model-success",
            execution=model_result,
            started_at=model_at,
        ),
    )
    return ObservabilityDashboard.build(records)


def main() -> int:
    report = build_report()
    summary = report.summary
    tokens = summary["tokens"]
    latency = summary["latency_ms"]

    print("=== T4.2 统一可观测面板（离线验收） ===")
    print("数据说明: Harness 与 Graph 为本次实际执行耗时；模型耗时由确定性 Mock 时钟产生。")
    print("事件时间说明: 旧 trace 只记录先后顺序，因此当前展示顺序，不伪造单事件耗时。")
    print("\n【总览】")
    print(
        f"运行次数: {summary['total_runs']} | "
        f"成功: {summary['succeeded_runs']} | "
        f"失败: {summary['failed_runs']} | "
        f"失败率: {summary['failure_rate_percent']:.2f}%"
    )
    print(
        f"Token 消耗: 输入={tokens['input']}，输出={tokens['output']}，总计={tokens['total']}"
    )
    print(
        f"耗时（毫秒）: 平均={latency['average']:.2f}，"
        f"P95={latency['p95']}，最大={latency['maximum']}"
    )

    print("\n【分层指标】")
    for layer, metrics in report.by_layer.items():
        print(
            f"- {LAYER_ZH[layer]}（{layer}）: "
            f"运行={metrics['total_runs']}，失败率={metrics['failure_rate_percent']:.2f}%，"
            f"Token={metrics['tokens']['total']}，平均耗时={metrics['latency_ms']['average']:.2f}ms"
        )

    print("\n【逐次调用链】")
    for record in report.records:
        status_zh = STATUS_ZH[record.status]
        print(
            f"\n[{record.run_id}] {LAYER_ZH[record.layer]}（{record.layer}）"
            f" / {record.component} / {status_zh}（{record.status}） / {record.duration_ms}ms"
        )
        if record.total_tokens:
            print(
                f"  Token: 输入={record.input_tokens}，输出={record.output_tokens}，总计={record.total_tokens}"
            )
        if record.error_message:
            print(f"  失败原因: {record.error_type}: {record.error_message}")
        for event in record.events:
            attempt = f" / 第{event.attempt}次" if event.attempt else ""
            detail = f" / {event.detail}" if event.detail else ""
            print(
                f"  {event.sequence:02d}. {event.event} -> "
                f"{STATUS_ZH[event.status]}（{event.status}） / {event.component}"
                f"{attempt}{detail}"
            )

    checks = {
        "可查看统一调用链": all(record.events for record in report.records),
        "可查看 Token 消耗": summary["tokens"]["total"] == 21,
        "可查看运行耗时": all(record.duration_ms >= 1 for record in report.records),
        "可计算真实失败率": summary["failure_rate_percent"] == 25.0,
        "失败前 trace 和原因未丢失": any(
            record.status == "failed" and record.error_message and record.events
            for record in report.records
        ),
    }
    print("\n【直观验收】")
    for name, passed in checks.items():
        print(f"- {'通过' if passed else '失败'}: {name}")
    print(f"结论: {'本切片验收通过' if all(checks.values()) else '本切片验收失败'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
