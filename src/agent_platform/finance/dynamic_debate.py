"""Optional LLM-authored debate language with deterministic evidence enforcement."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_platform.core import (
    DeepSeekChatAdapter,
    ModelGateway,
    ModelGatewayConfigurationError,
    ModelRequest,
    ModelRetryPolicy,
)
from agent_platform.llm_governance import GovernancePolicy, ModelGovernanceRuntime

from .structured_debate import (
    DEBATE_SIDES,
    StructuredDebateQuery,
    StructuredDebateRuntime,
    validate_structured_debate,
)


class DynamicDebateError(ValueError):
    """Dynamic debate configuration or candidate is invalid."""


class ModelGatewayPort(Protocol):
    def generate(self, request: ModelRequest) -> Any:
        """Generate one schema-constrained candidate."""


_ARGUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claim": {"type": "string", "minLength": 1},
        "evidence_ids": {
            "type": "array",
            "minItems": 2,
            "items": {"type": "string", "minLength": 4, "maxLength": 4},
        },
        "reasoning": {"type": "string", "minLength": 1},
    },
    "required": ["claim", "evidence_ids", "reasoning"],
    "additionalProperties": False,
}


DYNAMIC_DEBATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "rounds": {
            "type": "array",
            "minItems": 2,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "round": {"type": "integer", "minimum": 1, "maximum": 3},
                    "bull": deepcopy(_ARGUMENT_SCHEMA),
                    "bear": deepcopy(_ARGUMENT_SCHEMA),
                },
                "required": ["round", "bull", "bear"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rounds"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class DynamicDebateResult:
    report: Mapping[str, Any]
    mode: str
    provider: str
    model: str
    semantic_attempts: int
    fallback_reason: str | None
    usage: Mapping[str, int]
    latency_ms: int
    trace: tuple[Mapping[str, Any], ...]
    governance: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "report": deepcopy(dict(self.report)),
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "semantic_attempts": self.semantic_attempts,
            "fallback_reason": self.fallback_reason,
            "usage": dict(self.usage),
            "latency_ms": self.latency_ms,
            "trace": [deepcopy(dict(event)) for event in self.trace],
            "governance": deepcopy(dict(self.governance)),
            "safety": {
                "candidate_language_only": True,
                "deterministic_evidence_validation": True,
                "changes_synthesis": False,
                "changes_risk_controls": False,
                "real_trading_allowed": False,
            },
        }


class DynamicDebateRuntime:
    """Deep interface for model drafting, evidence replay, retry, and fallback."""

    def __init__(
        self,
        *,
        gateway: ModelGatewayPort | None,
        fallback: StructuredDebateRuntime | None = None,
        max_semantic_attempts: int = 2,
    ) -> None:
        if (
            isinstance(max_semantic_attempts, bool)
            or not isinstance(max_semantic_attempts, int)
            or max_semantic_attempts < 1
            or max_semantic_attempts > 3
        ):
            raise DynamicDebateError("max_semantic_attempts must be between 1 and 3")
        self._gateway = gateway
        self._fallback = fallback or StructuredDebateRuntime()
        self._max_semantic_attempts = max_semantic_attempts

    def run(self, query: StructuredDebateQuery) -> DynamicDebateResult:
        if not isinstance(query, StructuredDebateQuery):
            raise DynamicDebateError("query must be a StructuredDebateQuery")
        deterministic = self._fallback.run(query)
        catalog = _build_evidence_catalog(deterministic.report)
        trace: list[dict[str, Any]] = [
            {"event": "dynamic_debate.started", "detail": f"rounds={query.rounds}"},
            {"event": "dynamic_debate.evidence_catalog.created", "detail": f"items={len(catalog)}"},
        ]
        if self._gateway is None:
            return self._fallback_result(
                deterministic.report,
                trace,
                reason="未配置可用的 DeepSeek Model Gateway，已使用固定证据辩论。",
                attempts=0,
            )

        last_error = ""
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        total_latency = 0
        provider = "unknown"
        model = "unknown"
        governance: Mapping[str, Any] = {}
        for attempt in range(1, self._max_semantic_attempts + 1):
            trace.append({"event": "dynamic_debate.model_candidate.started", "attempt": attempt})
            try:
                gateway_result = self._gateway.generate(
                    _build_model_request(query, catalog, attempt=attempt, previous_error=last_error)
                )
                candidate_governance = getattr(gateway_result, "governance", None)
                if isinstance(candidate_governance, Mapping):
                    governance = dict(candidate_governance)
                response = gateway_result.response
                provider = str(response.provider)
                model = str(response.model)
                total_latency += int(response.latency_ms)
                for key in total_usage:
                    total_usage[key] += int(getattr(response.usage, key))
                report = _rehydrate_candidate(
                    response.structured_output,
                    query=query,
                    catalog=catalog,
                )
                validation = validate_structured_debate(report, query.combined_analysis)
                if not validation.valid:
                    raise DynamicDebateError(validation.detail)
                trace.extend(
                    [
                        {"event": "dynamic_debate.model_candidate.accepted", "attempt": attempt},
                        {"event": "dynamic_debate.completed", "detail": "dynamic language accepted after evidence replay"},
                    ]
                )
                return DynamicDebateResult(
                    report=report,
                    mode="dynamic",
                    provider=provider,
                    model=model,
                    semantic_attempts=attempt,
                    fallback_reason=None,
                    usage=total_usage,
                    latency_ms=total_latency,
                    trace=tuple(trace),
                    governance=governance,
                )
            except Exception as error:  # model output remains untrusted
                last_error = str(error)
                trace.append(
                    {
                        "event": "dynamic_debate.model_candidate.rejected",
                        "attempt": attempt,
                        "detail": last_error,
                    }
                )

        return self._fallback_result(
            deterministic.report,
            trace,
            reason=f"动态候选未通过证据复核（{last_error}），已使用固定证据辩论。",
            attempts=self._max_semantic_attempts,
            provider=provider,
            model=model,
            usage=total_usage,
            latency_ms=total_latency,
            governance=governance,
        )

    @staticmethod
    def _fallback_result(
        report: Mapping[str, Any],
        trace: list[dict[str, Any]],
        *,
        reason: str,
        attempts: int,
        provider: str = "local",
        model: str = "deterministic-debate",
        usage: Mapping[str, int] | None = None,
        latency_ms: int = 0,
        governance: Mapping[str, Any] | None = None,
    ) -> DynamicDebateResult:
        trace.append({"event": "dynamic_debate.fallback.used", "detail": reason})
        return DynamicDebateResult(
            report=report,
            mode="deterministic_fallback",
            provider=provider,
            model=model,
            semantic_attempts=attempts,
            fallback_reason=reason,
            usage=usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            latency_ms=latency_ms,
            trace=tuple(trace),
            governance=dict(governance or {}),
        )


def _build_evidence_catalog(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for debate_round in report["rounds"]:
        for side in DEBATE_SIDES:
            for reference in debate_round[side]["evidence"]:
                by_path.setdefault(str(reference["path"]), deepcopy(dict(reference)))
    catalog: list[dict[str, Any]] = []
    for index, reference in enumerate(by_path.values(), start=1):
        catalog.append({"id": f"E{index:03d}", **reference})
    return catalog


def _build_model_request(
    query: StructuredDebateQuery,
    catalog: list[dict[str, Any]],
    *,
    attempt: int,
    previous_error: str,
) -> ModelRequest:
    context = {
        "symbol": query.symbol,
        "round_count": query.rounds,
        "evidence_catalog": catalog,
        "retry_feedback": previous_error[:500] if attempt > 1 else "",
    }
    return ModelRequest(
        prompt=json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        system_prompt=(
            "你是证券研究中的 Bull/Bear 辩论写作者，只能改写论证语言。每方每轮至少选择两个"
            " evidence_id，且整场每方必须覆盖至少两个不同 Specialist。只能使用目录中的证据，"
            "不能创造或修改数值、来源、时间和路径。Claim 和 Reasoning 中不要写任何数字、百分比、日期或价格，"
            "数值和时间只保留在 evidence 中；第二轮和第三轮必须回应上一轮对方观点。"
            "不能给出买卖指令、目标价、仓位或收益承诺。返回指定 JSON。"
        ),
        response_schema=DYNAMIC_DEBATE_SCHEMA,
        schema_name="dynamic_financial_debate",
        max_output_tokens=1400,
    )


def _rehydrate_candidate(
    candidate: Any,
    *,
    query: StructuredDebateQuery,
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise DynamicDebateError("model candidate must be an object")
    rounds = candidate.get("rounds")
    if not isinstance(rounds, list) or len(rounds) != query.rounds:
        raise DynamicDebateError("model candidate round count does not match request")
    by_id = {item["id"]: item for item in catalog}
    hydrated_rounds: list[dict[str, Any]] = []
    all_sources: set[str] = set()
    side_specialists = {side: set() for side in DEBATE_SIDES}
    for expected_number, item in enumerate(rounds, start=1):
        if not isinstance(item, Mapping) or item.get("round") != expected_number:
            raise DynamicDebateError("model candidate rounds must be sequential")
        hydrated: dict[str, Any] = {
            "round": expected_number,
            "format": "Claim → Evidence → Reasoning",
        }
        for side in DEBATE_SIDES:
            argument = item.get(side)
            if not isinstance(argument, Mapping):
                raise DynamicDebateError(f"round {expected_number} is missing {side}")
            evidence_ids = argument.get("evidence_ids")
            if not isinstance(evidence_ids, list) or len(evidence_ids) < 2:
                raise DynamicDebateError(f"round {expected_number} {side} needs at least two evidence ids")
            references: list[dict[str, Any]] = []
            for evidence_id in evidence_ids:
                if evidence_id not in by_id:
                    raise DynamicDebateError(f"unknown evidence id: {evidence_id}")
                reference = {key: deepcopy(value) for key, value in by_id[evidence_id].items() if key != "id"}
                references.append(reference)
                side_specialists[side].add(reference["specialist"])
                all_sources.update(reference["sources"])
            claim = str(argument.get("claim", "")).strip()
            reasoning = str(argument.get("reasoning", "")).strip()
            if not claim or not reasoning:
                raise DynamicDebateError(f"round {expected_number} {side} text is empty")
            _validate_text_numbers(claim, reasoning, references, expected_number)
            hydrated_argument: dict[str, Any] = {
                "id": f"{side}.r{expected_number}",
                "side": side,
                "claim": claim,
                "evidence": references,
                "reasoning": reasoning,
            }
            if expected_number > 1:
                other = "bear" if side == "bull" else "bull"
                hydrated_argument["counter_to"] = f"{other}.r{expected_number - 1}"
            hydrated[side] = hydrated_argument
        hydrated_rounds.append(hydrated)
    for side in DEBATE_SIDES:
        if len(side_specialists[side]) < 2:
            raise DynamicDebateError(f"{side} evidence cites fewer than two specialist agents")
    return {
        "status": "debate_completed",
        "symbol": query.symbol,
        "mode": query.mode,
        "rounds": hydrated_rounds,
        "evidence_balance": {
            "bull_specialists": sorted(side_specialists["bull"]),
            "bear_specialists": sorted(side_specialists["bear"]),
            "minimum_specialists_per_side": 2,
            "single_sided_evidence": False,
        },
        "sources": sorted(all_sources),
        "next_stage": "synthesis_and_regime_gate",
        "caveats": [
            "LLM only drafted claim and reasoning language; evidence was rehydrated and replayed by deterministic code",
            "synthesis, price interval, position sizing, and risk controls remain unchanged",
        ],
    }


_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?")
_BLOCKED_PHRASES = ("绝对稳赚", "100%收益", "保证收益", "立即买入", "立即卖出", "已经下单")


def _validate_text_numbers(
    claim: str,
    reasoning: str,
    references: list[Mapping[str, Any]],
    round_number: int,
) -> None:
    combined_text = f"{claim} {reasoning}"
    for phrase in _BLOCKED_PHRASES:
        if phrase in combined_text:
            raise DynamicDebateError(f"candidate text contains blocked phrase: {phrase}")
    allowed: set[str] = set()
    for reference in references:
        allowed.update(_NUMBER_PATTERN.findall(json.dumps(reference["value"], ensure_ascii=False)))
    used = set(_NUMBER_PATTERN.findall(combined_text))
    invented = sorted(used - allowed)
    if invented:
        raise DynamicDebateError(f"candidate text contains unsupported numbers: {', '.join(invented)}")


def build_default_dynamic_debate_runtime() -> DynamicDebateRuntime:
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return DynamicDebateRuntime(gateway=None)
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    try:
        gateway = ModelGateway(
            DeepSeekChatAdapter.from_env(model=model),
            retry_policy=ModelRetryPolicy(
                max_attempts=2,
                timeout_seconds=30,
                initial_backoff_seconds=0.25,
            ),
        )
    except ModelGatewayConfigurationError:
        return DynamicDebateRuntime(gateway=None)
    governed_gateway = ModelGovernanceRuntime(
        gateway,
        policy=GovernancePolicy(
            policy_version="p7-dynamic-debate-policy-v1",
            prompt_version="dynamic-debate-prompt-v1",
            schema_version="dynamic-debate-schema-v1",
            route="deepseek",
            max_calls=3,
            max_total_tokens=7200,
            max_output_tokens=1400,
            cache_ttl_seconds=300,
        ),
    )
    return DynamicDebateRuntime(gateway=governed_gateway)


__all__ = [
    "DYNAMIC_DEBATE_SCHEMA",
    "DynamicDebateError",
    "DynamicDebateResult",
    "DynamicDebateRuntime",
    "ModelGatewayPort",
    "build_default_dynamic_debate_runtime",
]
