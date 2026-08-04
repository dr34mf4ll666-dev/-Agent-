"""离线演示五类 Guardrail 的配置、通过和拦截 trace。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.core import (  # noqa: E402
    AgentHarness,
    AgentRequest,
    AgentResponse,
    CrossValidationResult,
    GuardrailRegistry,
    HarnessExecutionError,
)


REPORT_SCHEMA = {
    "type": "object",
    "required": [
        "subject",
        "positive_points",
        "risk_points",
        "score",
        "source",
        "timestamp",
        "as_of",
    ],
    "properties": {
        "subject": {"type": "string", "minLength": 1},
        "positive_points": {"type": "integer", "minimum": 0},
        "risk_points": {"type": "integer", "minimum": 0},
        "score": {"type": "integer", "minimum": -100, "maximum": 100},
        "source": {"type": "string", "minLength": 1},
        "timestamp": {"type": "string", "minLength": 1},
        "as_of": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


class SafeReportAgent:
    name = "safe_report"

    def run(self, request: AgentRequest) -> AgentResponse:
        report = {
            "subject": request.context["subject"],
            "positive_points": 70,
            "risk_points": 20,
            "score": 50,
            "source": "synthetic_fixture",
            "timestamp": "2026-08-04T12:00:00+08:00",
            "as_of": "2026-08-04T00:00:00+08:00",
        }
        return AgentResponse(
            content="报告已完成，结论基于离线示例，不构成任何保证。",
            metadata={"report": report},
        )


class MisleadingReportAgent(SafeReportAgent):
    name = "misleading_report"

    def run(self, request: AgentRequest) -> AgentResponse:
        safe = super().run(request)
        return AgentResponse(
            content="这个方案绝对稳赚，并且可以获得100%收益。",
            metadata=safe.metadata,
        )


def validate_score(report: object) -> CrossValidationResult:
    if not isinstance(report, dict):
        return CrossValidationResult(False, "report must be an object")
    expected = report["positive_points"] - report["risk_points"]
    return CrossValidationResult(
        valid=report["score"] == expected,
        detail=f"score must equal {expected}",
    )


def build_guardrails():
    registry = GuardrailRegistry.with_builtins(
        cross_validators={"score_formula": validate_score}
    )
    return registry.build(
        [
            {
                "type": "json_schema",
                "output_schema": REPORT_SCHEMA,
                "output_path": "metadata.report",
            },
            {
                "type": "source_attribution",
                "required_fields": ["source", "timestamp", "as_of"],
                "output_paths": ["metadata.report"],
            },
            {
                "type": "rate_limiter",
                "max_calls": 2,
                "period_seconds": 60,
            },
            {
                "type": "keyword_blocker",
                "blocked_keywords": ["绝对稳赚", "100%收益"],
            },
            {
                "type": "cross_validator",
                "validator": "score_formula",
                "output_path": "metadata.report",
            },
        ]
    )


def print_trace(trace) -> None:
    for event in trace:
        suffix = f" ({event.detail})" if event.detail else ""
        print(f"- {event.event}{suffix}")


def main() -> int:
    guardrails = build_guardrails()
    request = AgentRequest(
        task="生成一份结构化示例报告",
        context={"subject": "通用 Agent 平台"},
    )

    print("=== 五类 Guardrail 离线演示 ===")
    print("已加载:", ", ".join(item.name for item in guardrails))

    safe_result = AgentHarness(SafeReportAgent(), guardrails).run(request)
    print("\n安全输出: passed")
    print(safe_result.response.metadata["report"])
    print_trace(safe_result.trace)

    try:
        AgentHarness(MisleadingReportAgent(), guardrails).run(request)
    except HarnessExecutionError as error:
        print("\n关键词拦截: postflight failed")
        print(error.cause)
        print_trace(error.trace)
    else:
        print("关键词拦截没有生效")
        return 1

    try:
        AgentHarness(SafeReportAgent(), guardrails).run(request)
    except HarnessExecutionError as error:
        print("\n限流拦截: preflight failed")
        print(error.cause)
        print_trace(error.trace)
    else:
        print("限流拦截没有生效")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
