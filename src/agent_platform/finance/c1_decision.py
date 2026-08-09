"""C1 final slice: synthesis, quality checks, and Market Regime gating."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from agent_platform.core import CrossValidationResult

from .combined_analysis import (
    CombinedAnalysisQuery,
    CombinedAnalysisRuntime,
    build_default_combined_analysis_runtime,
)
from .data_hub import FinancialDataPolicy
from .structured_debate import (
    StructuredDebateQuery,
    StructuredDebateRuntime,
    build_default_structured_debate_runtime,
    validate_structured_debate,
)


SPECIALIST_WEIGHTS = {
    "technical": Decimal("0.25"),
    "fundamental": Decimal("0.30"),
    "industry": Decimal("0.20"),
    "macro": Decimal("0.25"),
}
BEARISH_REGIMES = {"bearish", "bear", "risk_off"}
MIXED_REGIMES = {"mixed", "neutral"}
LOW_RISK_APPETITES = {"low", "cautious"}


class C1DecisionError(ValueError):
    """The C1 decision request or deterministic result is invalid."""


def _read_path(root: Any, path: str) -> Any:
    current = root
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index < len(current):
                current = current[index]
                continue
        raise C1DecisionError(f"required C1 field is missing: {path}")
    return current


def _decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise C1DecisionError(f"{field} must be a decimal-compatible number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise C1DecisionError(f"{field} must be a decimal-compatible number") from error
    if not result.is_finite():
        raise C1DecisionError(f"{field} must be finite")
    return result


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _percent(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _bundle_from_report(value: Mapping[str, Any]) -> Mapping[str, Any]:
    bundle = value.get("combined_analysis")
    if isinstance(bundle, Mapping) and bundle.get("status") == "specialists_completed":
        return bundle
    return value


@dataclass(frozen=True)
class C1DecisionQuery:
    """Small interface for running the complete C1 research decision slice."""

    combined_query: CombinedAnalysisQuery
    debate_rounds: int = 2
    base_position_cap_percent: int = 30

    def __post_init__(self) -> None:
        if not isinstance(self.combined_query, CombinedAnalysisQuery):
            raise C1DecisionError("combined_query must be a CombinedAnalysisQuery")
        if (
            isinstance(self.debate_rounds, bool)
            or not isinstance(self.debate_rounds, int)
            or self.debate_rounds not in (2, 3)
        ):
            raise C1DecisionError("debate_rounds must be 2 or 3")
        if (
            isinstance(self.base_position_cap_percent, bool)
            or not isinstance(self.base_position_cap_percent, int)
            or not 0 < self.base_position_cap_percent <= 100
        ):
            raise C1DecisionError(
                "base_position_cap_percent must be an integer between 1 and 100"
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "C1DecisionQuery":
        if not isinstance(value, Mapping):
            raise C1DecisionError("C1 decision query must be an object")
        if "combined_query" not in value:
            raise C1DecisionError("C1 decision query is missing combined_query")
        return cls(
            combined_query=CombinedAnalysisQuery.from_mapping(value["combined_query"]),
            debate_rounds=value.get("debate_rounds", 2),
            base_position_cap_percent=value.get("base_position_cap_percent", 30),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "combined_query": self.combined_query.to_mapping(),
            "debate_rounds": self.debate_rounds,
            "base_position_cap_percent": self.base_position_cap_percent,
        }


def _score_map(bundle: Mapping[str, Any]) -> dict[str, Decimal]:
    reports = bundle["reports"]
    return {
        "technical": _decimal(
            reports["technical"]["signal_score"], field="technical.signal_score"
        ),
        "fundamental": _decimal(
            reports["fundamental"]["score"], field="fundamental.score"
        ),
        "industry": _decimal(reports["industry"]["score"], field="industry.score"),
        "macro": _decimal(reports["macro"]["score"], field="macro.score"),
    }


def _weighted_score(scores: Mapping[str, Decimal]) -> Decimal:
    return sum(
        (scores[name] * SPECIALIST_WEIGHTS[name] for name in SPECIALIST_WEIGHTS),
        Decimal("0"),
    )


def _consistency_check(
    bundle: Mapping[str, Any],
    debate: Mapping[str, Any],
) -> dict[str, Any]:
    reports = bundle.get("reports")
    if not isinstance(reports, Mapping):
        raise C1DecisionError("combined report is missing reports")

    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    expected_symbol = bundle.get("symbol")
    for specialist in ("technical", "fundamental", "macro"):
        report = reports.get(specialist)
        if not isinstance(report, Mapping):
            raise C1DecisionError(f"combined report is missing {specialist}")
        if report.get("symbol") != expected_symbol:
            raise C1DecisionError(
                f"consistency check failed: {specialist} symbol does not match"
            )
        for field in ("sources", "as_of", "timestamp"):
            value = report.get(field)
            if (
                not value
                or (field == "sources" and not isinstance(value, list))
                or (field != "sources" and not isinstance(value, str))
            ):
                raise C1DecisionError(
                    f"consistency check failed: {specialist}.{field} is incomplete"
                )
        checks.append(
            {
                "name": f"{specialist}_identity_and_provenance",
                "status": "passed",
            }
        )

    technical_price = _decimal(
        reports["technical"]["latest_close"], field="technical.latest_close"
    )
    fundamental_price = _decimal(
        reports["fundamental"]["valuation"]["current_price"],
        field="fundamental.valuation.current_price",
    )
    reference_price = (technical_price + fundamental_price) / Decimal("2")
    price_gap = abs(technical_price - fundamental_price) / reference_price
    if price_gap > Decimal("0.05"):
        raise C1DecisionError(
            "consistency check failed: technical and fundamental prices differ by more than 5%"
        )
    checks.append(
        {
            "name": "reference_price_alignment",
            "status": "passed",
            "technical_price": str(technical_price),
            "fundamental_price": str(fundamental_price),
            "gap_percent": _percent(price_gap * Decimal("100")),
        }
    )

    scores = _score_map(bundle)
    positive = [name for name, score in scores.items() if score > Decimal("10")]
    negative = [name for name, score in scores.items() if score < Decimal("-10")]
    directional_disagreement = bool(positive and negative)
    if directional_disagreement:
        warnings.append(
            "specialist scores are directionally mixed; synthesis must lower confidence"
        )
    checks.append(
        {
            "name": "specialist_direction_consistency",
            "status": "warning" if directional_disagreement else "passed",
            "positive_specialists": positive,
            "negative_specialists": negative,
        }
    )

    debate_validation = validate_structured_debate(debate, bundle)
    if not debate_validation.valid:
        raise C1DecisionError(
            f"consistency check failed: debate evidence is invalid: {debate_validation.detail}"
        )
    checks.append({"name": "debate_evidence_replay", "status": "passed"})
    return {
        "status": "passed",
        "valid": True,
        "checks": checks,
        "warnings": warnings,
        "directional_disagreement": directional_disagreement,
        "reference_price": _money(reference_price),
    }


def _bias_detector(
    bundle: Mapping[str, Any],
    debate: Mapping[str, Any],
) -> dict[str, Any]:
    balance = debate.get("evidence_balance")
    if not isinstance(balance, Mapping):
        raise C1DecisionError("bias detector failed: evidence balance is missing")
    bull_specialists = balance.get("bull_specialists")
    bear_specialists = balance.get("bear_specialists")
    single_sided = balance.get("single_sided_evidence")
    if (
        not isinstance(bull_specialists, list)
        or not isinstance(bear_specialists, list)
        or len(set(bull_specialists)) < 2
        or len(set(bear_specialists)) < 2
        or single_sided is not False
    ):
        raise C1DecisionError(
            "bias detector failed: both sides need at least two Specialist sources"
        )
    source_count = len(set(bundle.get("sources", [])))
    if source_count < 4:
        raise C1DecisionError(
            "bias detector failed: combined evidence has insufficient source diversity"
        )
    checks = [
        {
            "name": "bull_evidence_balance",
            "status": "passed",
            "specialists": sorted(set(bull_specialists)),
        },
        {
            "name": "bear_evidence_balance",
            "status": "passed",
            "specialists": sorted(set(bear_specialists)),
        },
        {
            "name": "source_diversity",
            "status": "passed",
            "source_count": source_count,
        },
        {
            "name": "single_sided_evidence",
            "status": "passed",
            "value": False,
        },
    ]
    return {
        "status": "passed",
        "valid": True,
        "checks": checks,
        "single_sided_evidence": False,
    }


def _market_regime_gate(
    macro_report: Mapping[str, Any],
    raw_inclination: str,
    base_position_cap_percent: int,
) -> dict[str, Any]:
    regime = str(macro_report["market_regime"]["label"])
    risk_appetite = str(macro_report["risk_appetite"]["label"])
    base_cap = Decimal(str(base_position_cap_percent))
    if regime in BEARISH_REGIMES:
        effective_cap = min(base_cap, Decimal("10"))
        regime_rule = "bearish regime caps the research position at 10%"
    elif regime in MIXED_REGIMES:
        effective_cap = min(base_cap, Decimal("20"))
        regime_rule = "mixed regime caps the research position at 20%"
    else:
        effective_cap = base_cap
        regime_rule = "non-bearish regime keeps the configured base cap"
    if risk_appetite in LOW_RISK_APPETITES:
        effective_cap = min(effective_cap, Decimal("15"))
        risk_rule = "low or cautious risk appetite caps the research position at 15%"
    else:
        risk_rule = "risk appetite does not add a cap"

    if raw_inclination == "positive" and effective_cap < base_cap:
        gated_inclination = "cautious_positive"
    elif raw_inclination == "negative":
        gated_inclination = "negative"
    else:
        gated_inclination = raw_inclination
    return {
        "status": "reduced" if effective_cap < base_cap else "normal",
        "regime": regime,
        "risk_appetite": risk_appetite,
        "base_position_cap_percent": str(base_cap),
        "effective_position_cap_percent": str(effective_cap),
        "raw_inclination": raw_inclination,
        "gated_inclination": gated_inclination,
        "rule": f"{regime_rule}; {risk_rule}",
        "real_trading_allowed": False,
    }


def _synthesis(
    bundle: Mapping[str, Any],
    debate: Mapping[str, Any],
    consistency: Mapping[str, Any],
    bias: Mapping[str, Any],
    base_position_cap_percent: int,
) -> dict[str, Any]:
    scores = _score_map(bundle)
    weighted = _weighted_score(scores)
    if weighted >= Decimal("20"):
        raw_inclination = "positive"
    elif weighted <= Decimal("-20"):
        raw_inclination = "negative"
    else:
        raw_inclination = "neutral"

    reference_price = _decimal(
        consistency["reference_price"], field="consistency.reference_price"
    )
    positive_support = sum(
        (max(score, Decimal("0")) for score in scores.values()), Decimal("0")
    )
    risk_pressure = max(Decimal("0"), -scores["macro"]) + max(
        Decimal("0"), -scores["technical"]
    )
    bull_buffer = Decimal("0.05") + positive_support / Decimal("1000")
    bear_buffer = Decimal("0.05") + risk_pressure / Decimal("1000")
    bull_upper = reference_price * (Decimal("1") + bull_buffer)
    bear_lower = reference_price * (Decimal("1") - bear_buffer)

    market_gate = _market_regime_gate(
        bundle["reports"]["macro"],
        raw_inclination,
        base_position_cap_percent,
    )
    confidence = Decimal("50")
    confidence += Decimal("15") if consistency["valid"] else Decimal("0")
    confidence += Decimal("15") if bias["valid"] else Decimal("0")
    confidence += min(Decimal("15"), abs(weighted) / Decimal("5"))
    if consistency["directional_disagreement"]:
        confidence -= Decimal("5")
    if market_gate["status"] == "reduced":
        confidence -= Decimal("10")
    confidence_int = int(max(Decimal("0"), min(Decimal("100"), confidence)))

    return {
        "inclination": market_gate["gated_inclination"],
        "raw_inclination": raw_inclination,
        "weighted_score": str(weighted.quantize(Decimal("0.01"))),
        "confidence": confidence_int,
        "confidence_meaning": "confidence in evidence consistency, not probability of profit",
        "target_price_interval": {
            "lower": _money(bear_lower),
            "reference": _money(reference_price),
            "upper": _money(bull_upper),
            "method": (
                "reference price is the mean of technical latest_close and fundamental "
                "current_price; buffers are deterministic score-based research bounds"
            ),
        },
        "side_targets": {
            "bull_target_price_upper": _money(bull_upper),
            "bear_target_price_lower": _money(bear_lower),
        },
        "score_inputs": {name: str(value) for name, value in scores.items()},
        "market_regime_gate": market_gate,
        "evidence_basis": {
            "debate_rounds": len(debate["rounds"]),
            "sources": sorted(set(bundle["sources"])),
            "consistency_status": consistency["status"],
            "bias_status": bias["status"],
        },
        "caveats": [
            "target prices are deterministic research bounds, not forecasts or investment advice",
            "confidence is not a return probability",
            "position cap is a C1 Market Regime gate, not the full C2 Risk Manager",
        ],
    }


def validate_c1_decision(value: Any) -> CrossValidationResult:
    """Validate the complete C1 result through its public output shape."""

    if not isinstance(value, Mapping):
        return CrossValidationResult(False, "C1 decision must be an object")
    if value.get("status") != "c1_completed":
        return CrossValidationResult(False, "C1 decision has an invalid status")
    bundle = value.get("combined_analysis")
    debate = value.get("debate")
    synthesis = value.get("synthesis")
    quality = value.get("quality")
    gate = value.get("market_regime_gate")
    if not all(isinstance(item, Mapping) for item in (bundle, debate, synthesis, quality, gate)):
        return CrossValidationResult(False, "C1 decision sections must be objects")
    if value.get("symbol") != bundle.get("symbol") or value.get("mode") != bundle.get("mode"):
        return CrossValidationResult(False, "C1 decision identity does not match bundle")
    confidence = synthesis.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int)
        or not 0 <= confidence <= 100
    ):
        return CrossValidationResult(False, "C1 confidence must be between 0 and 100")
    interval = synthesis.get("target_price_interval")
    if not isinstance(interval, Mapping):
        return CrossValidationResult(False, "C1 target price interval is missing")
    try:
        lower = _decimal(interval["lower"], field="target lower")
        reference = _decimal(interval["reference"], field="target reference")
        upper = _decimal(interval["upper"], field="target upper")
    except (KeyError, C1DecisionError) as error:
        return CrossValidationResult(False, str(error))
    if not lower <= reference <= upper:
        return CrossValidationResult(False, "C1 target price interval is not ordered")
    side_targets = synthesis.get("side_targets")
    if (
        not isinstance(side_targets, Mapping)
        or side_targets.get("bull_target_price_upper") != interval.get("upper")
        or side_targets.get("bear_target_price_lower") != interval.get("lower")
    ):
        return CrossValidationResult(False, "C1 side target prices do not match synthesis")
    for section_name in ("consistency_check", "bias_detector"):
        section = quality.get(section_name)
        if not isinstance(section, Mapping) or section.get("valid") is not True:
            return CrossValidationResult(False, f"{section_name} did not pass")
    try:
        base_cap = _decimal(
            gate["base_position_cap_percent"], field="base position cap"
        )
        effective_cap = _decimal(
            gate["effective_position_cap_percent"], field="effective position cap"
        )
    except (KeyError, C1DecisionError) as error:
        return CrossValidationResult(False, str(error))
    if effective_cap > base_cap:
        return CrossValidationResult(False, "Market Regime gate increased the base cap")
    return CrossValidationResult(True)


@dataclass(frozen=True)
class C1DecisionResult:
    """Complete C1 report plus specialist Graph and stage trace."""

    report: Mapping[str, Any]
    specialist_graph: Mapping[str, Any]
    trace: tuple[Mapping[str, Any], ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "report": deepcopy(dict(self.report)),
            "specialist_graph": deepcopy(dict(self.specialist_graph)),
            "trace": [deepcopy(dict(event)) for event in self.trace],
        }


class C1DecisionRuntime:
    """Deep C1 seam: specialists, debate, quality checks, and synthesis."""

    def __init__(
        self,
        *,
        combined_runtime: CombinedAnalysisRuntime,
        debate_runtime: StructuredDebateRuntime,
    ) -> None:
        self._combined_runtime = combined_runtime
        self._debate_runtime = debate_runtime

    def run(self, query: C1DecisionQuery) -> C1DecisionResult:
        if not isinstance(query, C1DecisionQuery):
            raise C1DecisionError("query must be a C1DecisionQuery")
        trace: list[dict[str, Any]] = [
            {
                "event": "c1.started",
                "detail": f"symbol={query.combined_query.symbol}; mode={query.combined_query.mode}",
            }
        ]
        combined = self._combined_runtime.run(query.combined_query).to_mapping()
        bundle = combined["report"]
        trace.append(
            {
                "event": "c1.specialists.completed",
                "detail": "four Specialist reports and Graph evidence are ready",
            }
        )
        debate = self._debate_runtime.run(
            StructuredDebateQuery(bundle, rounds=query.debate_rounds)
        ).to_mapping()
        debate_report = debate["report"]
        trace.append(
            {
                "event": "c1.debate.completed",
                "detail": f"rounds={len(debate_report['rounds'])}",
            }
        )
        consistency = _consistency_check(bundle, debate_report)
        bias = _bias_detector(bundle, debate_report)
        trace.append(
            {
                "event": "c1.quality.checked",
                "detail": "consistency and bias checks passed",
            }
        )
        synthesis = _synthesis(
            bundle,
            debate_report,
            consistency,
            bias,
            query.base_position_cap_percent,
        )
        trace.append(
            {
                "event": "c1.synthesis.completed",
                "detail": (
                    f"inclination={synthesis['inclination']}; "
                    f"confidence={synthesis['confidence']}"
                ),
            }
        )
        report = {
            "status": "c1_completed",
            "symbol": bundle["symbol"],
            "mode": bundle["mode"],
            "combined_analysis": bundle,
            "debate": debate_report,
            "synthesis": synthesis,
            "quality": {
                "status": "passed",
                "consistency_check": consistency,
                "bias_detector": bias,
            },
            "market_regime_gate": synthesis["market_regime_gate"],
            "sources": sorted(
                set(bundle["sources"]) | set(debate_report["sources"])
            ),
            "next_stage": "trader_and_risk_manager",
            "caveats": [
                "C1 produces a research conclusion and Market Regime gate, not an order",
                "Trader, full Risk Manager, backtest, and real trading are outside C1",
            ],
        }
        validation = validate_c1_decision(report)
        if not validation.valid:
            raise C1DecisionError(validation.detail)
        trace.append(
            {
                "event": "c1.completed",
                "detail": "synthesis, quality checks, and regime gate passed",
            }
        )
        return C1DecisionResult(
            report=report,
            specialist_graph=combined["graph"],
            trace=tuple(trace),
        )

    def run_graph_node(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(state, Mapping):
            raise C1DecisionError("C1 graph state must be an object")
        return {"c1_decision": self.run(C1DecisionQuery.from_mapping(state)).to_mapping()}


def build_default_c1_decision_runtime(
    *,
    project_root: str | Path | None = None,
    policy: FinancialDataPolicy | None = None,
) -> C1DecisionRuntime:
    return C1DecisionRuntime(
        combined_runtime=build_default_combined_analysis_runtime(
            project_root=project_root,
            policy=policy,
        ),
        debate_runtime=build_default_structured_debate_runtime(),
    )


__all__ = [
    "C1DecisionError",
    "C1DecisionQuery",
    "C1DecisionResult",
    "C1DecisionRuntime",
    "build_default_c1_decision_runtime",
    "validate_c1_decision",
]
