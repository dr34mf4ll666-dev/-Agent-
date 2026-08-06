"""Model-backed agents for planning local research and synthesizing evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent_platform.core import (
    Action,
    AgentRequest,
    AgentResponse,
    CognitiveLoopState,
    ModelGateway,
    ModelAdapterResponse,
    ModelRequest,
    ModelRetryPolicy,
    ModelUsage,
    MockModelAdapter,
    Observation,
    Plan,
    Reflection,
    ReflectionDecision,
)

from .contracts import ResearchContractError, require_text, require_timestamp


PLAN_SCHEMA = {
    "type": "object",
    "required": ["goal", "steps"],
    "properties": {
        "goal": {"type": "string", "minLength": 1},
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": False,
}

ACTION_SCHEMA = {
    "type": "object",
    "required": ["tool", "arguments", "rationale"],
    "properties": {
        "tool": {"const": "local_document_search"},
        "arguments": {
            "type": "object",
            "required": ["query", "limit"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "additionalProperties": False,
        },
        "rationale": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}

REFLECTION_SCHEMA = {
    "type": "object",
    "required": ["decision", "reason", "final_answer"],
    "properties": {
        "decision": {"enum": ["continue", "revise", "complete"]},
        "reason": {"type": "string", "minLength": 1},
        "final_answer": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}

REPORT_DRAFT_SCHEMA = {
    "type": "object",
    "required": ["topic", "summary", "findings"],
    "properties": {
        "topic": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "findings": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["claim", "evidence_ids"],
                "properties": {
                    "claim": {"type": "string", "minLength": 1},
                    "evidence_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def build_offline_research_gateway(topic: str) -> ModelGateway:
    """Create the deterministic four-call model script used by tests and demos."""

    normalized_topic = require_text(topic, "topic")
    outputs = (
        {
            "goal": f"根据本地资料研究 {normalized_topic}",
            "steps": ["检索资料", "整理证据", "形成摘要"],
        },
        {
            "tool": "local_document_search",
            "arguments": {"query": "Python 代码评审", "limit": 3},
            "rationale": "先查找与主题直接相关的本地资料",
        },
        {
            "decision": "complete",
            "reason": "检索已返回带来源的本地资料",
            "final_answer": "资料检索完成，交给 Graph 整理证据",
        },
        {
            "topic": normalized_topic,
            "summary": "高质量代码评审应先对齐需求与测试，再检查边界和异常处理。",
            "findings": [
                {
                    "claim": "评审应先核对需求和测试，并关注边界条件与异常处理。",
                    "evidence_ids": ["E1"],
                }
            ],
        },
    )
    script = [
        ModelAdapterResponse(
            content=json.dumps(output, ensure_ascii=False),
            structured_output=output,
            model="mock-research-v1",
            usage=ModelUsage(input_tokens=20, output_tokens=12, total_tokens=32),
            response_id=f"mock-research-{index}",
        )
        for index, output in enumerate(outputs, start=1)
    ]
    return ModelGateway(
        MockModelAdapter(model="mock-research-v1", script=script),
        retry_policy=ModelRetryPolicy(
            max_attempts=2,
            timeout_seconds=20,
            initial_backoff_seconds=0,
        ),
    )


def _model_call_record(result: Any) -> dict[str, Any]:
    response = result.response
    return {
        "provider": response.provider,
        "model": response.model,
        "response_id": response.response_id,
        "attempts": response.attempts,
        "latency_ms": response.latency_ms,
        "tokens": {
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
            "total": response.usage.total_tokens,
        },
        "trace": [
            {
                "event": event.event,
                "attempt": event.attempt,
                "detail": event.detail,
            }
            for event in result.trace
        ],
    }


class GatewayResearchPlanner:
    """Use ModelGateway at each seam of the existing cognitive loop."""

    name = "gateway_research_planner"

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway
        self.model_calls: list[dict[str, Any]] = []

    def create_plan(self, request: AgentRequest) -> Plan:
        payload = self._generate(
            prompt=(
                f"研究主题：{request.task}\n"
                "请制定一个很短的本地资料研究计划。"
            ),
            schema=PLAN_SCHEMA,
            schema_name="research_plan",
        )
        return Plan(goal=payload["goal"], steps=tuple(payload["steps"]))

    def choose_action(self, state: CognitiveLoopState) -> Action:
        payload = self._generate(
            prompt=(
                f"研究主题：{state.request.task}\n"
                f"当前步骤：{state.step_count + 1}\n"
                "只能选择 local_document_search。请给出检索词和 1 到 3 的 limit。"
            ),
            schema=ACTION_SCHEMA,
            schema_name="research_action",
        )
        return Action(
            tool=payload["tool"],
            arguments=payload["arguments"],
            rationale=payload["rationale"],
        )

    def reflect(
        self,
        state: CognitiveLoopState,
        observation: Observation,
    ) -> Reflection:
        observation_payload = {
            "success": observation.success,
            "output": observation.output,
            "error": observation.error,
        }
        payload = self._generate(
            prompt=(
                f"研究主题：{state.request.task}\n"
                "检查本地检索结果。如果成功且至少有一条资料，选择 complete；"
                "否则选择 revise。\n"
                f"Observation：{json.dumps(observation_payload, ensure_ascii=False)}"
            ),
            schema=REFLECTION_SCHEMA,
            schema_name="research_reflection",
        )
        decision = ReflectionDecision(payload["decision"])
        final_answer = payload["final_answer"]
        if decision is ReflectionDecision.COMPLETE and not final_answer:
            raise ResearchContractError(
                "complete model reflection requires final_answer"
            )
        if decision is not ReflectionDecision.COMPLETE:
            final_answer = None
        return Reflection(
            decision=decision,
            reason=payload["reason"],
            final_answer=final_answer,
        )

    def _generate(
        self,
        *,
        prompt: str,
        schema: Mapping[str, Any],
        schema_name: str,
    ) -> Mapping[str, Any]:
        result = self._gateway.generate(
            ModelRequest(
                prompt=prompt,
                system_prompt=(
                    "你是受控的本地资料研究 Agent。只输出符合 Schema 的 JSON；"
                    "不得选择未授权工具，不得虚构工具结果。"
                ),
                response_schema=schema,
                schema_name=schema_name,
                max_output_tokens=320,
            )
        )
        self.model_calls.append(_model_call_record(result))
        payload = result.response.structured_output
        if not isinstance(payload, Mapping):
            raise ResearchContractError("model output must be an object")
        return payload


class GatewayResearchReporter:
    """Synthesize only the evidence supplied by the Graph state."""

    name = "gateway_research_reporter"

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway
        self.model_calls: list[dict[str, Any]] = []

    def run(self, request: AgentRequest) -> AgentResponse:
        topic = require_text(request.context.get("topic"), "topic")
        timestamp = require_timestamp(
            request.context.get("run_timestamp"),
            "run_timestamp",
        )
        evidence = request.context.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ResearchContractError("evidence must be a non-empty list")

        result = self._gateway.generate(
            ModelRequest(
                prompt=(
                    f"研究主题：{topic}\n"
                    "只根据下列证据生成简短摘要。每条结论必须引用 evidence_id；"
                    "不要添加证据之外的事实。\n"
                    f"证据：{json.dumps(evidence, ensure_ascii=False)}"
                ),
                system_prompt=(
                    "你是证据约束的研究报告 Agent。只输出符合 Schema 的 JSON。"
                ),
                response_schema=REPORT_DRAFT_SCHEMA,
                schema_name="research_report",
                max_output_tokens=700,
            )
        )
        self.model_calls.append(_model_call_record(result))
        draft = result.response.structured_output
        if not isinstance(draft, Mapping):
            raise ResearchContractError("report model output must be an object")
        report = {
            "topic": draft["topic"],
            "summary": draft["summary"],
            "findings": list(draft["findings"]),
            "source": f"model:{result.response.provider}",
            "timestamp": timestamp,
        }
        return AgentResponse(content=report["summary"], metadata={"report": report})
