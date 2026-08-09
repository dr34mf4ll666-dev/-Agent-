"""C1 second slice: evidence-backed Bull/Bear structured debate."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from agent_platform.core import CrossValidationResult


SPECIALIST_NAMES = ("technical", "fundamental", "industry", "macro")
DEBATE_SIDES = ("bull", "bear")


class StructuredDebateError(ValueError):
    """The structured-debate request or result is invalid."""


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
        raise StructuredDebateError(f"evidence path not found: {path}")
    return current


def _unwrap_bundle(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept either the aggregate report or the outer workflow result."""

    report = value.get("report")
    if isinstance(report, Mapping) and report.get("status") == "specialists_completed":
        return report
    return value


def _validate_bundle_shape(value: Mapping[str, Any]) -> CrossValidationResult:
    bundle = _unwrap_bundle(value)
    if bundle.get("status") != "specialists_completed":
        return CrossValidationResult(False, "debate input must be a completed specialist bundle")
    reports = bundle.get("reports")
    if not isinstance(reports, Mapping):
        return CrossValidationResult(False, "debate input is missing reports")
    for specialist in SPECIALIST_NAMES:
        report = reports.get(specialist)
        if not isinstance(report, Mapping):
            return CrossValidationResult(
                False, f"debate input is missing {specialist} report"
            )
        sources = report.get("sources")
        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(source, str) or not source.strip() for source in sources)
        ):
            return CrossValidationResult(
                False, f"{specialist} report must retain at least one source"
            )
        if not isinstance(report.get("as_of"), str) or not report["as_of"].strip():
            return CrossValidationResult(
                False, f"{specialist} report must retain as_of"
            )
    return CrossValidationResult(True)


@dataclass(frozen=True)
class StructuredDebateQuery:
    """Small interface for a deterministic evidence-backed debate."""

    combined_analysis: Mapping[str, Any]
    rounds: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.combined_analysis, Mapping):
            raise StructuredDebateError("combined_analysis must be an object")
        if (
            isinstance(self.rounds, bool)
            or not isinstance(self.rounds, int)
            or self.rounds < 2
            or self.rounds > 3
        ):
            raise StructuredDebateError("rounds must be an integer between 2 and 3")
        bundle = _unwrap_bundle(self.combined_analysis)
        validation = _validate_bundle_shape(bundle)
        if not validation.valid:
            raise StructuredDebateError(validation.detail)
        object.__setattr__(self, "combined_analysis", deepcopy(dict(bundle)))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StructuredDebateQuery":
        if not isinstance(value, Mapping):
            raise StructuredDebateError("structured debate query must be an object")
        if "combined_analysis" not in value:
            raise StructuredDebateError("structured debate query is missing combined_analysis")
        return cls(
            combined_analysis=value["combined_analysis"],
            rounds=value.get("rounds", 2),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "combined_analysis": deepcopy(dict(self.combined_analysis)),
            "rounds": self.rounds,
        }

    @property
    def symbol(self) -> str:
        return str(self.combined_analysis.get("symbol", ""))

    @property
    def mode(self) -> str:
        return str(self.combined_analysis.get("mode", ""))


def _evidence_ref(
    bundle: Mapping[str, Any],
    specialist: str,
    relative_path: str,
) -> dict[str, Any]:
    report = bundle["reports"][specialist]
    path = f"reports.{specialist}.{relative_path}"
    return {
        "specialist": specialist,
        "path": path,
        "value": deepcopy(_read_path(bundle, path)),
        "sources": list(report["sources"]),
        "as_of": report["as_of"],
    }


def _display(value: Any) -> str:
    if isinstance(value, Mapping):
        if "label" in value:
            return str(value["label"])
        if "score" in value:
            return str(value["score"])
    return str(value)


def _claim(
    *,
    claim_id: str,
    side: str,
    text: str,
    evidence: list[Mapping[str, Any]],
    reasoning: str,
    counter_to: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": claim_id,
        "side": side,
        "claim": text,
        "evidence": [deepcopy(dict(item)) for item in evidence],
        "reasoning": reasoning,
    }
    if counter_to is not None:
        result["counter_to"] = counter_to
    return result


def _build_round(bundle: Mapping[str, Any], number: int) -> dict[str, Any]:
    fundamental_score = _display(
        _read_path(bundle, "reports.fundamental.score")
    )
    fundamental_label = _display(
        _read_path(bundle, "reports.fundamental.score_label")
    )
    industry_score = _display(_read_path(bundle, "reports.industry.score"))
    prosperity = _display(
        _read_path(bundle, "reports.industry.prosperity.label")
    )
    sector_change = _display(
        _read_path(bundle, "reports.industry.prosperity.sector_change_percent")
    )
    technical_trend = _display(
        _read_path(bundle, "reports.technical.trend")
    )
    technical_signal = _display(
        _read_path(bundle, "reports.technical.signal_label")
    )
    macro_regime = _display(
        _read_path(bundle, "reports.macro.market_regime.label")
    )
    risk_appetite = _display(
        _read_path(bundle, "reports.macro.risk_appetite.label")
    )
    macro_score = _display(_read_path(bundle, "reports.macro.score"))
    valuation_percentile = _display(
        _read_path(bundle, "reports.fundamental.valuation.valuation_percentile")
    )
    growth = _display(
        _read_path(bundle, "reports.fundamental.growth.net_profit_growth_percent")
    )
    macd_hist = _display(
        _read_path(bundle, "reports.technical.macd.histogram")
    )

    bull_evidence = [
        _evidence_ref(bundle, "fundamental", "score_label"),
        _evidence_ref(bundle, "fundamental", "score"),
        _evidence_ref(bundle, "industry", "prosperity.label"),
        _evidence_ref(bundle, "industry", "score"),
    ]
    bear_evidence = [
        _evidence_ref(bundle, "technical", "trend"),
        _evidence_ref(bundle, "technical", "signal_label"),
        _evidence_ref(bundle, "macro", "market_regime.label"),
        _evidence_ref(bundle, "macro", "risk_appetite.label"),
    ]

    if number == 1:
        bull = _claim(
            claim_id="bull.r1",
            side="bull",
            text=(
                f"基本面和行业形成双重正向底稿：基本面标签为 {fundamental_label}"
                f"（评分 {fundamental_score}），行业景气度为 {prosperity}"
                f"（评分 {industry_score}）。"
            ),
            evidence=bull_evidence,
            reasoning="两类相互独立的 Specialist 都提供了正向证据，因此值得进入后续综合研判。",
        )
        bear = _claim(
            claim_id="bear.r1",
            side="bear",
            text=(
                f"技术面没有形成明确上行趋势（trend={technical_trend}，"
                f"signal={technical_signal}），宏观风险偏好为 {risk_appetite}，"
                f"不能直接把基本面正向结果转成买入结论。"
            ),
            evidence=bear_evidence,
            reasoning="技术方向和宏观风险偏好没有与基本面形成同向确认，仍存在等待确认的必要。",
        )
    elif number == 2:
        bull = _claim(
            claim_id="bull.r2",
            side="bull",
            text=(
                f"对上一轮风险的回应是：规则估值分位为 {valuation_percentile}，"
                f"行业板块变化为 {sector_change}%，说明正向证据不只来自单一评分。"
            ),
            evidence=[
                _evidence_ref(bundle, "fundamental", "valuation.valuation_percentile"),
                _evidence_ref(bundle, "industry", "prosperity.sector_change_percent"),
                _evidence_ref(bundle, "industry", "score"),
            ],
            reasoning="估值和行业表现提供了第二层支持，但仍只说明研究价值，不构成交易授权。",
            counter_to="bear.r1",
        )
        bear = _claim(
            claim_id="bear.r2",
            side="bear",
            text=(
                f"对上一轮正向观点的回应是：技术信号仍为 {technical_signal}，"
                f"宏观 Regime 为 {macro_regime}，宏观评分为 {macro_score}，"
                "主要风险尚未被反驳。"
            ),
            evidence=[
                _evidence_ref(bundle, "technical", "signal_label"),
                _evidence_ref(bundle, "macro", "market_regime.label"),
                _evidence_ref(bundle, "macro", "score"),
            ],
            reasoning="估值和行业强度不能替代趋势与市场环境确认，因此综合结论需要保守。",
            counter_to="bull.r1",
        )
    else:
        bull = _claim(
            claim_id="bull.r3",
            side="bull",
            text=(
                f"最终支持理由是：净利润增长代理为 {growth}%，行业评分为 {industry_score}，"
                f"技术 MACD 柱值为 {macd_hist}；这些证据支持继续跟踪而非立即否定。"
            ),
            evidence=[
                _evidence_ref(bundle, "fundamental", "growth.net_profit_growth_percent"),
                _evidence_ref(bundle, "industry", "score"),
                _evidence_ref(bundle, "technical", "macd.histogram"),
            ],
            reasoning="多来源证据仍有正向部分，但本轮只形成候选观点，目标价和仓位必须留给后续模块。",
            counter_to="bear.r2",
        )
        bear = _claim(
            claim_id="bear.r3",
            side="bear",
            text=(
                f"最终风险理由是：技术趋势仍为 {technical_trend}，宏观评分为 {macro_score}，"
                f"风险偏好为 {risk_appetite}；当前证据不足以证明单边行情。"
            ),
            evidence=[
                _evidence_ref(bundle, "technical", "trend"),
                _evidence_ref(bundle, "macro", "score"),
                _evidence_ref(bundle, "macro", "risk_appetite.label"),
            ],
            reasoning="风险证据跨越技术和宏观两个维度，足以阻止辩论层直接输出买卖指令。",
            counter_to="bull.r2",
        )

    return {
        "round": number,
        "format": "Claim → Evidence → Reasoning",
        "bull": bull,
        "bear": bear,
    }


def validate_structured_debate(
    value: Any,
    combined_analysis: Mapping[str, Any],
) -> CrossValidationResult:
    """Validate claims through the same evidence seam used by the runtime."""

    if not isinstance(value, Mapping):
        return CrossValidationResult(False, "structured debate must be an object")
    bundle = _unwrap_bundle(combined_analysis)
    bundle_validation = _validate_bundle_shape(bundle)
    if not bundle_validation.valid:
        return bundle_validation
    if value.get("status") != "debate_completed":
        return CrossValidationResult(False, "structured debate has an invalid status")
    if value.get("symbol") != bundle.get("symbol") or value.get("mode") != bundle.get("mode"):
        return CrossValidationResult(False, "debate identity does not match specialist bundle")
    rounds = value.get("rounds")
    if not isinstance(rounds, list) or len(rounds) not in (2, 3):
        return CrossValidationResult(False, "debate must contain 2 or 3 rounds")

    claim_ids: set[str] = set()
    previous_claim_ids: set[str] = set()
    side_specialists = {side: set() for side in DEBATE_SIDES}
    sources: set[str] = set()
    for expected_number, debate_round in enumerate(rounds, start=1):
        if not isinstance(debate_round, Mapping):
            return CrossValidationResult(False, f"round {expected_number} must be an object")
        if debate_round.get("round") != expected_number:
            return CrossValidationResult(False, "debate rounds must be sequential")
        for side in DEBATE_SIDES:
            claim = debate_round.get(side)
            if not isinstance(claim, Mapping):
                return CrossValidationResult(False, f"round {expected_number} is missing {side}")
            claim_id = claim.get("id")
            if (
                not isinstance(claim_id, str)
                or not claim_id
                or claim.get("side") != side
                or claim_id in claim_ids
            ):
                return CrossValidationResult(False, f"round {expected_number} has an invalid {side} claim")
            if not isinstance(claim.get("claim"), str) or not claim["claim"].strip():
                return CrossValidationResult(False, f"{claim_id} is missing claim text")
            if not isinstance(claim.get("reasoning"), str) or not claim["reasoning"].strip():
                return CrossValidationResult(False, f"{claim_id} is missing reasoning")
            if expected_number > 1 and claim.get("counter_to") not in previous_claim_ids:
                return CrossValidationResult(False, f"{claim_id} must counter a prior claim")
            evidence = claim.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                return CrossValidationResult(False, f"{claim_id} is missing evidence")
            for reference in evidence:
                if not isinstance(reference, Mapping):
                    return CrossValidationResult(False, f"{claim_id} contains invalid evidence")
                specialist = reference.get("specialist")
                path = reference.get("path")
                reference_sources = reference.get("sources")
                if specialist not in SPECIALIST_NAMES or not isinstance(path, str):
                    return CrossValidationResult(False, f"{claim_id} contains an invalid evidence path")
                expected_prefix = f"reports.{specialist}."
                if not path.startswith(expected_prefix):
                    return CrossValidationResult(False, f"{claim_id} evidence path crosses specialist seam")
                try:
                    expected_value = _read_path(bundle, path)
                except StructuredDebateError as error:
                    return CrossValidationResult(False, str(error))
                if reference.get("value") != expected_value:
                    return CrossValidationResult(False, f"{claim_id} evidence value does not match report")
                report_sources = bundle["reports"][specialist]["sources"]
                if (
                    not isinstance(reference_sources, list)
                    or not reference_sources
                    or not set(reference_sources).issubset(set(report_sources))
                    or reference.get("as_of") != bundle["reports"][specialist]["as_of"]
                ):
                    return CrossValidationResult(False, f"{claim_id} evidence provenance is invalid")
                side_specialists[side].add(specialist)
                sources.update(reference_sources)
            claim_ids.add(claim_id)
        previous_claim_ids = set(claim_ids)

    for side in DEBATE_SIDES:
        if len(side_specialists[side]) < 2:
            return CrossValidationResult(
                False, f"{side} evidence cites fewer than two specialist agents"
            )
    if not isinstance(value.get("sources"), list) or set(value["sources"]) != sources:
        return CrossValidationResult(False, "debate source aggregation is incomplete")
    balance = value.get("evidence_balance")
    if not isinstance(balance, Mapping) or balance.get("single_sided_evidence") is not False:
        return CrossValidationResult(False, "debate evidence balance is invalid")
    return CrossValidationResult(True)


@dataclass(frozen=True)
class StructuredDebateResult:
    """Structured debate report and observable deterministic trace."""

    report: Mapping[str, Any]
    trace: tuple[Mapping[str, Any], ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "report": deepcopy(dict(self.report)),
            "trace": [deepcopy(dict(event)) for event in self.trace],
        }


class StructuredDebateRuntime:
    """Deep seam for deterministic, evidence-backed Bull/Bear rounds."""

    def run(self, query: StructuredDebateQuery) -> StructuredDebateResult:
        if not isinstance(query, StructuredDebateQuery):
            raise StructuredDebateError("query must be a StructuredDebateQuery")
        bundle = query.combined_analysis
        trace: list[dict[str, Any]] = [
            {
                "event": "debate.started",
                "detail": f"symbol={query.symbol}; rounds={query.rounds}",
            }
        ]
        debate_rounds: list[dict[str, Any]] = []
        all_sources: set[str] = set()
        for number in range(1, query.rounds + 1):
            trace.append(
                {
                    "event": "debate.round.started",
                    "round": number,
                    "detail": "bull and bear claims are generated from report evidence",
                }
            )
            debate_round = _build_round(bundle, number)
            debate_rounds.append(debate_round)
            for side in DEBATE_SIDES:
                claim = debate_round[side]
                all_sources.update(
                    source
                    for reference in claim["evidence"]
                    for source in reference["sources"]
                )
                trace.append(
                    {
                        "event": "debate.claim.created",
                        "round": number,
                        "side": side,
                        "claim_id": claim["id"],
                        "detail": f"evidence_count={len(claim['evidence'])}",
                    }
                )

        side_specialists = {
            side: sorted(
                {
                    reference["specialist"]
                    for debate_round in debate_rounds
                    for reference in debate_round[side]["evidence"]
                }
            )
            for side in DEBATE_SIDES
        }
        report = {
            "status": "debate_completed",
            "symbol": query.symbol,
            "mode": query.mode,
            "rounds": debate_rounds,
            "evidence_balance": {
                "bull_specialists": side_specialists["bull"],
                "bear_specialists": side_specialists["bear"],
                "minimum_specialists_per_side": 2,
                "single_sided_evidence": False,
            },
            "sources": sorted(all_sources),
            "next_stage": "synthesis_and_regime_gate",
            "caveats": [
                "this deterministic slice structures and checks debate evidence but does not produce a final investment conclusion",
                "target-price interval, confidence, Market Regime gate, and Trader/Risk Manager are not implemented",
            ],
        }
        validation = validate_structured_debate(report, bundle)
        if not validation.valid:
            raise StructuredDebateError(validation.detail)
        trace.append(
            {
                "event": "debate.cross_validation.passed",
                "detail": "claim paths, values, provenance, and evidence balance verified",
            }
        )
        trace.append({"event": "debate.completed", "detail": "structured debate ready for synthesis"})
        return StructuredDebateResult(report=report, trace=tuple(trace))

    def run_graph_node(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(state, Mapping) or "combined_analysis" not in state:
            raise StructuredDebateError("graph state is missing combined_analysis")
        query = StructuredDebateQuery(
            combined_analysis=state["combined_analysis"],
            rounds=state.get("debate_rounds", 2),
        )
        return {"structured_debate": self.run(query).to_mapping()}


def build_default_structured_debate_runtime() -> StructuredDebateRuntime:
    return StructuredDebateRuntime()


__all__ = [
    "StructuredDebateError",
    "StructuredDebateQuery",
    "StructuredDebateResult",
    "StructuredDebateRuntime",
    "build_default_structured_debate_runtime",
    "validate_structured_debate",
]
