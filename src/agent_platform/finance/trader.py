"""C2 first slice: deterministic simulation-only Trader candidate signals."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from agent_platform.core import (
    AgentHarness,
    AgentRequest,
    AgentResponse,
    CrossValidationResult,
    CrossValidator,
    JSONSchemaValidator,
)

from .c1_decision import validate_c1_decision


POSITIVE_INCLINATIONS = {"positive", "cautious_positive"}
NEGATIVE_INCLINATIONS = {"negative", "cautious_negative"}


TRADER_CANDIDATE_SCHEMA = {
    "type": "object",
    "required": [
        "status",
        "symbol",
        "mode",
        "signal",
        "target_price_interval",
        "confidence",
        "market_context",
        "provenance",
        "execution",
        "next_stage",
        "caveats",
    ],
    "properties": {
        "status": {"const": "candidate_signal_created"},
        "symbol": {"type": "string", "minLength": 1},
        "mode": {"enum": ["offline", "live"]},
        "signal": {
            "type": "object",
            "required": ["action", "label", "rule", "weighted_score"],
            "properties": {
                "action": {"enum": ["buy", "sell", "hold"]},
                "label": {"type": "string", "minLength": 1},
                "rule": {"type": "string", "minLength": 1},
                "weighted_score": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        "target_price_interval": {"type": "object"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "market_context": {
            "type": "object",
            "required": [
                "regime",
                "risk_appetite",
                "position_cap_percent",
            ],
            "properties": {
                "regime": {"type": "string", "minLength": 1},
                "risk_appetite": {"type": "string", "minLength": 1},
                "position_cap_percent": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        "provenance": {
            "type": "object",
            "required": ["sources", "timestamp", "as_of"],
            "properties": {
                "sources": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
                "timestamp": {"type": "object"},
                "as_of": {"type": "object"},
            },
            "additionalProperties": False,
        },
        "execution": {
            "type": "object",
            "required": [
                "simulation_only",
                "order_created",
                "real_trading_allowed",
                "requires_risk_review",
                "human_confirmation_required",
                "status",
            ],
            "properties": {
                "simulation_only": {"const": True},
                "order_created": {"const": False},
                "real_trading_allowed": {"const": False},
                "requires_risk_review": {"const": True},
                "human_confirmation_required": {"type": "boolean"},
                "status": {"enum": ["awaiting_risk_review", "no_action"]},
            },
            "additionalProperties": False,
        },
        "next_stage": {"const": "risk_manager"},
        "caveats": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": False,
}


class TraderError(ValueError):
    """The C2 Trader request or candidate signal is invalid."""


def _unwrap_c1(value: Mapping[str, Any]) -> Mapping[str, Any]:
    report = value.get("report")
    if isinstance(report, Mapping) and report.get("status") == "c1_completed":
        return report
    return value


def _decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise TraderError(f"{field} must be a decimal-compatible number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise TraderError(f"{field} must be a decimal-compatible number") from error
    if not result.is_finite():
        raise TraderError(f"{field} must be finite")
    return result


@dataclass(frozen=True)
class TraderQuery:
    """Stable input seam for converting one complete C1 report into a candidate."""

    c1_decision: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.c1_decision, Mapping):
            raise TraderError("c1_decision must be an object")
        report = _unwrap_c1(self.c1_decision)
        validation = validate_c1_decision(report)
        if not validation.valid:
            raise TraderError(f"invalid C1 decision: {validation.detail}")
        object.__setattr__(self, "c1_decision", deepcopy(dict(report)))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TraderQuery":
        if not isinstance(value, Mapping) or "c1_decision" not in value:
            raise TraderError("Trader graph state is missing c1_decision")
        return cls(c1_decision=value["c1_decision"])

    def to_mapping(self) -> dict[str, Any]:
        return {"c1_decision": deepcopy(dict(self.c1_decision))}


def _expected_action(c1: Mapping[str, Any]) -> tuple[str, str, str]:
    synthesis = c1["synthesis"]
    inclination = str(synthesis["inclination"])
    weighted_score = _decimal(
        synthesis["weighted_score"], field="synthesis.weighted_score"
    )
    confidence = synthesis["confidence"]
    if (
        inclination in POSITIVE_INCLINATIONS
        and weighted_score >= Decimal("20")
        and confidence >= 60
    ):
        return (
            "buy",
            "模拟买入候选",
            "positive inclination, weighted score >= 20, and confidence >= 60",
        )
    if (
        inclination in NEGATIVE_INCLINATIONS
        and weighted_score <= Decimal("-20")
        and confidence >= 60
    ):
        return (
            "sell",
            "模拟卖出候选",
            "negative inclination, weighted score <= -20, and confidence >= 60",
        )
    return (
        "hold",
        "模拟持有/观望",
        "direction, weighted score, and confidence do not jointly pass an action threshold",
    )


def _build_candidate(c1: Mapping[str, Any]) -> dict[str, Any]:
    synthesis = c1["synthesis"]
    combined = c1["combined_analysis"]
    gate = c1["market_regime_gate"]
    action, label, rule = _expected_action(c1)
    position_cap = _decimal(
        gate["effective_position_cap_percent"],
        field="market_regime_gate.effective_position_cap_percent",
    )
    actionable = action in {"buy", "sell"}
    reports = combined["reports"]
    return {
        "status": "candidate_signal_created",
        "symbol": c1["symbol"],
        "mode": c1["mode"],
        "signal": {
            "action": action,
            "label": label,
            "rule": rule,
            "weighted_score": synthesis["weighted_score"],
        },
        "target_price_interval": deepcopy(synthesis["target_price_interval"]),
        "confidence": synthesis["confidence"],
        "market_context": {
            "regime": gate["regime"],
            "risk_appetite": gate["risk_appetite"],
            "position_cap_percent": gate["effective_position_cap_percent"],
        },
        "provenance": {
            "sources": sorted(set(c1["sources"])),
            "timestamp": {
                name: reports[name]["timestamp"]
                for name in ("technical", "fundamental", "industry", "macro")
            },
            "as_of": {
                name: reports[name]["as_of"]
                for name in ("technical", "fundamental", "industry", "macro")
            },
        },
        "execution": {
            "simulation_only": True,
            "order_created": False,
            "real_trading_allowed": False,
            "requires_risk_review": True,
            "human_confirmation_required": actionable and position_cap > Decimal("10"),
            "status": "awaiting_risk_review" if actionable else "no_action",
        },
        "next_stage": "risk_manager",
        "caveats": [
            "this is a simulation-only candidate signal, not an order",
            "the target interval is inherited from C1 research bounds, not a forecast",
            "position sizing and final permission belong to the Risk Manager",
        ],
    }


def validate_trader_candidate(
    value: Any,
    c1_decision: Mapping[str, Any],
) -> CrossValidationResult:
    """Recompute and validate the Trader output against its C1 evidence."""

    if not isinstance(value, Mapping):
        return CrossValidationResult(False, "Trader candidate must be an object")
    c1 = _unwrap_c1(c1_decision)
    c1_validation = validate_c1_decision(c1)
    if not c1_validation.valid:
        return CrossValidationResult(False, f"invalid C1 decision: {c1_validation.detail}")
    try:
        expected = _build_candidate(c1)
    except (KeyError, TraderError) as error:
        return CrossValidationResult(False, str(error))
    for field in (
        "status",
        "symbol",
        "mode",
        "signal",
        "target_price_interval",
        "confidence",
        "market_context",
        "provenance",
        "execution",
        "next_stage",
        "caveats",
    ):
        if value.get(field) != expected[field]:
            return CrossValidationResult(
                False, f"Trader candidate field does not match deterministic result: {field}"
            )
    return CrossValidationResult(True)


class _DeterministicTraderAgent:
    name = "simulation_trader"

    def run(self, request: AgentRequest) -> AgentResponse:
        c1 = request.context.get("c1_decision")
        if not isinstance(c1, Mapping):
            raise TraderError("Trader Agent requires c1_decision context")
        candidate = _build_candidate(c1)
        return AgentResponse(
            content=f"{candidate['signal']['action']} candidate created for risk review",
            metadata={"trade_candidate": candidate},
        )


@dataclass(frozen=True)
class TraderResult:
    """Simulation-only candidate plus Harness and stage traces."""

    report: Mapping[str, Any]
    harness_trace: tuple[Mapping[str, Any], ...]
    trace: tuple[Mapping[str, Any], ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "report": deepcopy(dict(self.report)),
            "harness_trace": [deepcopy(dict(event)) for event in self.harness_trace],
            "trace": [deepcopy(dict(event)) for event in self.trace],
        }


class TraderRuntime:
    """Deep C2 Trader seam: validate C1, derive a candidate, and forbid orders."""

    def run(self, query: TraderQuery) -> TraderResult:
        if not isinstance(query, TraderQuery):
            raise TraderError("query must be a TraderQuery")
        c1 = query.c1_decision
        trace: list[dict[str, Any]] = [
            {
                "event": "trader.started",
                "detail": f"symbol={c1['symbol']}; mode={c1['mode']}",
            },
            {
                "event": "trader.c1.validated",
                "detail": "complete C1 decision accepted",
            },
        ]
        harness = AgentHarness(
            _DeterministicTraderAgent(),
            guardrails=(
                JSONSchemaValidator(
                    output_schema=TRADER_CANDIDATE_SCHEMA,
                    output_path="metadata.trade_candidate",
                    name="trader_output_schema",
                ),
                CrossValidator(
                    lambda candidate: validate_trader_candidate(candidate, c1),
                    output_path="metadata.trade_candidate",
                    name="trader_candidate_recompute",
                ),
            ),
        )
        harness_result = harness.run(
            AgentRequest(
                task="Create one simulation-only candidate signal from C1",
                context={"c1_decision": deepcopy(dict(c1))},
            )
        )
        report = harness_result.response.metadata["trade_candidate"]
        trace.extend(
            (
                {
                    "event": "trader.candidate.created",
                    "detail": f"action={report['signal']['action']}",
                },
                {
                    "event": "trader.risk_handoff.ready",
                    "detail": "candidate requires Risk Manager review; no order created",
                },
                {
                    "event": "trader.completed",
                    "detail": "simulation-only candidate passed Harness validation",
                },
            )
        )
        return TraderResult(
            report=report,
            harness_trace=tuple(
                {
                    "event": event.event,
                    "agent": event.agent,
                    "detail": event.detail,
                }
                for event in harness_result.trace
            ),
            trace=tuple(trace),
        )

    def run_graph_node(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        query = TraderQuery.from_mapping(state)
        return {"trader_candidate": self.run(query).to_mapping()}


def build_default_trader_runtime() -> TraderRuntime:
    return TraderRuntime()


__all__ = [
    "TRADER_CANDIDATE_SCHEMA",
    "TraderError",
    "TraderQuery",
    "TraderResult",
    "TraderRuntime",
    "build_default_trader_runtime",
    "validate_trader_candidate",
]
