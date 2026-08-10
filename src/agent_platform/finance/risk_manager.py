"""Complete C2 deterministic Risk Manager and Trader-to-risk composition."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
from .trader import (
    TraderQuery,
    TraderRuntime,
    build_default_trader_runtime,
    validate_trader_candidate,
)


MAX_SINGLE_TRADE_LOSS_PERCENT = Decimal("2")
MAX_SECTOR_EXPOSURE_PERCENT = Decimal("30")
DRAWDOWN_REDUCTION_TRIGGER_PERCENT = Decimal("15")
MIN_AVERAGE_DAILY_TURNOVER = Decimal("10000000")
MAX_TURNOVER_PARTICIPATION_PERCENT = Decimal("10")
MIN_REWARD_RISK_RATIO = Decimal("1.5")


RISK_REVIEW_SCHEMA = {
    "type": "object",
    "required": [
        "status",
        "symbol",
        "mode",
        "risk_decision",
        "position",
        "price_controls",
        "risk_checks",
        "limits",
        "provenance",
        "execution",
        "next_stage",
        "caveats",
    ],
    "properties": {
        "status": {"const": "risk_review_completed"},
        "symbol": {"type": "string", "minLength": 1},
        "mode": {"enum": ["offline", "live"]},
        "risk_decision": {
            "type": "object",
            "required": ["status", "requested_action", "approved_action", "reason"],
            "properties": {
                "status": {
                    "enum": [
                        "approved",
                        "adjusted",
                        "pending_human_confirmation",
                        "blocked",
                        "forced_reduction",
                        "no_action",
                    ]
                },
                "requested_action": {"enum": ["buy", "sell", "hold"]},
                "approved_action": {"enum": ["buy", "sell", "hold", "reduce"]},
                "reason": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        "position": {"type": "object"},
        "price_controls": {"type": "object"},
        "risk_checks": {"type": "array", "minItems": 9},
        "limits": {"type": "object"},
        "provenance": {"type": "object"},
        "execution": {
            "type": "object",
            "required": [
                "simulation_only",
                "simulation_execution_allowed",
                "order_created",
                "real_trading_allowed",
                "human_confirmation_required",
                "human_confirmed",
            ],
            "properties": {
                "simulation_only": {"const": True},
                "simulation_execution_allowed": {"type": "boolean"},
                "order_created": {"const": False},
                "real_trading_allowed": {"const": False},
                "human_confirmation_required": {"type": "boolean"},
                "human_confirmed": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "next_stage": {"const": "complete_financial_graph"},
        "caveats": {"type": "array", "minItems": 1},
    },
    "additionalProperties": False,
}


class RiskManagerError(ValueError):
    """The Risk Manager input or deterministic review is invalid."""


def _decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise RiskManagerError(f"{field} must be a decimal-compatible number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise RiskManagerError(f"{field} must be a decimal-compatible number") from error
    if not result.is_finite():
        raise RiskManagerError(f"{field} must be finite")
    return result


def _percent(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_evaluation_time(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RiskManagerError("evaluation_time must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise RiskManagerError(
            "evaluation_time must be a timezone-aware ISO timestamp"
        ) from error
    if parsed.utcoffset() != timedelta(hours=8):
        raise RiskManagerError("evaluation_time must use Asia/Shanghai offset +08:00")
    return parsed


def _is_a_share_session(value: str) -> bool:
    moment = _parse_evaluation_time(value)
    if moment.weekday() >= 5:
        return False
    local_time = moment.timetz().replace(tzinfo=None)
    return time(9, 30) <= local_time <= time(11, 30) or time(13, 0) <= local_time <= time(15, 0)


@dataclass(frozen=True)
class RiskContext:
    """Portfolio and execution facts required by the deterministic risk rules."""

    account_equity: Decimal | str | int
    current_position_percent: Decimal | str | int
    requested_position_percent: Decimal | str | int
    sector_exposure_other_percent: Decimal | str | int
    current_drawdown_percent: Decimal | str | int
    average_daily_turnover: Decimal | str | int
    evaluation_time: str
    stop_loss_price: Decimal | str | int | None = None
    take_profit_price: Decimal | str | int | None = None
    human_confirmed: bool = False

    def __post_init__(self) -> None:
        decimal_fields = (
            "account_equity",
            "current_position_percent",
            "requested_position_percent",
            "sector_exposure_other_percent",
            "current_drawdown_percent",
            "average_daily_turnover",
        )
        for field in decimal_fields:
            object.__setattr__(self, field, _decimal(getattr(self, field), field=field))
        if self.account_equity <= 0:
            raise RiskManagerError("account_equity must be greater than zero")
        for field in (
            "current_position_percent",
            "requested_position_percent",
            "sector_exposure_other_percent",
            "current_drawdown_percent",
        ):
            value = getattr(self, field)
            if not Decimal("0") <= value <= Decimal("100"):
                raise RiskManagerError(f"{field} must be between 0 and 100")
        if self.average_daily_turnover < 0:
            raise RiskManagerError("average_daily_turnover must not be negative")
        _parse_evaluation_time(self.evaluation_time)
        for field in ("stop_loss_price", "take_profit_price"):
            value = getattr(self, field)
            if value is not None:
                normalized = _decimal(value, field=field)
                if normalized <= 0:
                    raise RiskManagerError(f"{field} must be greater than zero")
                object.__setattr__(self, field, normalized)
        if not isinstance(self.human_confirmed, bool):
            raise RiskManagerError("human_confirmed must be a boolean")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RiskContext":
        if not isinstance(value, Mapping):
            raise RiskManagerError("risk_context must be an object")
        required = (
            "account_equity",
            "current_position_percent",
            "requested_position_percent",
            "sector_exposure_other_percent",
            "current_drawdown_percent",
            "average_daily_turnover",
            "evaluation_time",
        )
        missing = [field for field in required if field not in value]
        if missing:
            raise RiskManagerError(f"risk_context is missing {missing[0]}")
        return cls(
            account_equity=value["account_equity"],
            current_position_percent=value["current_position_percent"],
            requested_position_percent=value["requested_position_percent"],
            sector_exposure_other_percent=value["sector_exposure_other_percent"],
            current_drawdown_percent=value["current_drawdown_percent"],
            average_daily_turnover=value["average_daily_turnover"],
            evaluation_time=value["evaluation_time"],
            stop_loss_price=value.get("stop_loss_price"),
            take_profit_price=value.get("take_profit_price"),
            human_confirmed=value.get("human_confirmed", False),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "account_equity": str(self.account_equity),
            "current_position_percent": str(self.current_position_percent),
            "requested_position_percent": str(self.requested_position_percent),
            "sector_exposure_other_percent": str(self.sector_exposure_other_percent),
            "current_drawdown_percent": str(self.current_drawdown_percent),
            "average_daily_turnover": str(self.average_daily_turnover),
            "evaluation_time": self.evaluation_time,
            "stop_loss_price": _money(self.stop_loss_price),
            "take_profit_price": _money(self.take_profit_price),
            "human_confirmed": self.human_confirmed,
        }


@dataclass(frozen=True)
class RiskManagerQuery:
    c1_decision: Mapping[str, Any]
    trader_candidate: Mapping[str, Any]
    risk_context: RiskContext

    def __post_init__(self) -> None:
        if not isinstance(self.c1_decision, Mapping):
            raise RiskManagerError("c1_decision must be an object")
        c1 = self.c1_decision.get("report", self.c1_decision)
        if not isinstance(c1, Mapping):
            raise RiskManagerError("c1_decision report must be an object")
        c1_validation = validate_c1_decision(c1)
        if not c1_validation.valid:
            raise RiskManagerError(f"invalid C1 decision: {c1_validation.detail}")
        if not isinstance(self.trader_candidate, Mapping):
            raise RiskManagerError("trader_candidate must be an object")
        candidate = self.trader_candidate.get("report", self.trader_candidate)
        if not isinstance(candidate, Mapping):
            raise RiskManagerError("trader_candidate must be an object")
        candidate_validation = validate_trader_candidate(candidate, c1)
        if not candidate_validation.valid:
            raise RiskManagerError(
                f"invalid Trader candidate: {candidate_validation.detail}"
            )
        if not isinstance(self.risk_context, RiskContext):
            raise RiskManagerError("risk_context must be a RiskContext")
        object.__setattr__(self, "c1_decision", deepcopy(dict(c1)))
        object.__setattr__(self, "trader_candidate", deepcopy(dict(candidate)))


def _risk_check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _build_risk_review(query: RiskManagerQuery) -> dict[str, Any]:
    c1 = query.c1_decision
    candidate = query.trader_candidate
    context = query.risk_context
    action = candidate["signal"]["action"]
    interval = candidate["target_price_interval"]
    reference = _decimal(interval["reference"], field="target reference")
    market_cap = _decimal(
        candidate["market_context"]["position_cap_percent"],
        field="market position cap",
    )
    session_open = _is_a_share_session(context.evaluation_time)
    regime = candidate["market_context"]["regime"]
    stop = context.stop_loss_price
    take = context.take_profit_price
    stop_distance_percent = Decimal("0")
    reward_risk_ratio = Decimal("0")
    stop_valid = True
    if action == "buy":
        stop_valid = stop is not None and take is not None and stop < reference < take
        if stop_valid:
            stop_distance_percent = (reference - stop) / reference * Decimal("100")
            reward_risk_ratio = (take - reference) / (reference - stop)
            stop_valid = reward_risk_ratio >= MIN_REWARD_RISK_RATIO

    if stop_distance_percent > 0:
        risk_cap = (
            MAX_SINGLE_TRADE_LOSS_PERCENT / stop_distance_percent * Decimal("100")
        )
    else:
        risk_cap = Decimal("100") if action != "buy" else Decimal("0")
    sector_cap = max(
        Decimal("0"),
        MAX_SECTOR_EXPOSURE_PERCENT - context.sector_exposure_other_percent,
    )
    if context.average_daily_turnover > 0:
        liquidity_cap = (
            context.average_daily_turnover
            * (MAX_TURNOVER_PARTICIPATION_PERCENT / Decimal("100"))
            / context.account_equity
            * Decimal("100")
        )
    else:
        liquidity_cap = Decimal("0")
    liquidity_valid = context.average_daily_turnover >= MIN_AVERAGE_DAILY_TURNOVER
    regime_allows_buy = str(regime).lower() not in {"bearish", "bear", "risk_off"}
    drawdown_triggered = (
        context.current_drawdown_percent > DRAWDOWN_REDUCTION_TRIGGER_PERCENT
    )

    checks = [
        _risk_check("trader_candidate", "passed", "candidate matches the C1 report"),
        _risk_check(
            "trading_session",
            "passed" if action == "hold" or session_open else "failed",
            "A-share session is open" if session_open else "outside A-share trading session",
        ),
        _risk_check(
            "market_regime",
            "passed" if action != "buy" or regime_allows_buy else "failed",
            f"regime={regime}",
        ),
        _risk_check(
            "liquidity",
            "passed" if action != "buy" or liquidity_valid else "failed",
            f"average_daily_turnover={context.average_daily_turnover}",
        ),
        _risk_check(
            "stop_loss_take_profit",
            "passed" if action != "buy" or stop_valid else "failed",
            "not applicable to non-buy candidate"
            if action != "buy"
            else f"reward_risk_ratio={_percent(reward_risk_ratio)}",
        ),
        _risk_check(
            "drawdown",
            "triggered" if drawdown_triggered else "passed",
            f"current={context.current_drawdown_percent}%; trigger=>15%",
        ),
    ]

    approved_position = context.current_position_percent
    approved_action = "hold"
    decision_status = "no_action"
    reason = "Trader candidate is hold; no exposure change is permitted"
    simulation_allowed = False

    if action == "sell":
        if session_open:
            approved_position = Decimal("0")
            approved_action = "sell"
            decision_status = "approved"
            reason = "risk-reducing exit candidate approved for simulation"
            simulation_allowed = True
        else:
            decision_status = "blocked"
            reason = "sell candidate is outside the A-share trading session"
    elif drawdown_triggered and context.current_position_percent > 0:
        approved_position = context.current_position_percent / Decimal("2")
        approved_action = "reduce"
        decision_status = "forced_reduction"
        reason = "portfolio drawdown exceeded 15%; target position is reduced by 50%"
        simulation_allowed = session_open
    elif action == "buy" and drawdown_triggered:
        approved_position = Decimal("0")
        decision_status = "blocked"
        reason = "portfolio drawdown exceeded 15%; new exposure is blocked"
    elif action == "buy":
        hard_failures = []
        if not session_open:
            hard_failures.append("trading_session")
        if not regime_allows_buy:
            hard_failures.append("market_regime")
        if not liquidity_valid:
            hard_failures.append("liquidity")
        if not stop_valid:
            hard_failures.append("stop_loss_take_profit")
        if hard_failures:
            decision_status = "blocked"
            reason = "blocked by " + ", ".join(hard_failures)
        else:
            approved_position = min(
                context.requested_position_percent,
                market_cap,
                risk_cap,
                sector_cap,
                liquidity_cap,
                Decimal("100"),
            )
            if approved_position <= context.current_position_percent:
                approved_action = "hold"
                decision_status = "no_action"
                reason = "risk-adjusted target does not increase the current position"
            else:
                approved_action = "buy"
                adjusted = approved_position < context.requested_position_percent
                decision_status = "adjusted" if adjusted else "approved"
                reason = (
                    "requested position was reduced by deterministic risk caps"
                    if adjusted
                    else "candidate passed deterministic risk caps"
                )
                simulation_allowed = True

    estimated_loss_percent = (
        approved_position / Decimal("100") * stop_distance_percent
        if action == "buy" and stop_valid
        else Decimal("0")
    )
    final_sector_exposure = (
        context.sector_exposure_other_percent + approved_position
        if approved_action == "buy"
        else context.sector_exposure_other_percent
    )
    checks.extend(
        (
            _risk_check(
                "single_trade_loss",
                "passed" if estimated_loss_percent <= MAX_SINGLE_TRADE_LOSS_PERCENT else "failed",
                f"estimated={_percent(estimated_loss_percent)}%; limit=2%",
            ),
            _risk_check(
                "sector_exposure",
                "passed" if final_sector_exposure <= MAX_SECTOR_EXPOSURE_PERCENT else "failed",
                f"final={_percent(final_sector_exposure)}%; limit=30%",
            ),
        )
    )

    human_required = approved_action == "buy" and approved_position > Decimal("10")
    if human_required and not context.human_confirmed and decision_status in {"approved", "adjusted"}:
        decision_status = "pending_human_confirmation"
        reason = "approved position exceeds 10% and requires explicit human confirmation"
        simulation_allowed = False
    checks.extend(
        (
            _risk_check(
                "human_confirmation",
                "passed"
                if not human_required or context.human_confirmed
                else "pending",
                "required above 10% approved position",
            ),
            _risk_check(
                "real_trading_disabled",
                "passed",
                "real trading is hard-disabled",
            ),
        )
    )

    return {
        "status": "risk_review_completed",
        "symbol": candidate["symbol"],
        "mode": candidate["mode"],
        "risk_decision": {
            "status": decision_status,
            "requested_action": action,
            "approved_action": approved_action,
            "reason": reason,
        },
        "position": {
            "current_percent": _percent(context.current_position_percent),
            "requested_percent": _percent(context.requested_position_percent),
            "approved_percent": _percent(approved_position),
            "market_regime_cap_percent": _percent(market_cap),
            "single_trade_risk_cap_percent": _percent(risk_cap),
            "sector_cap_percent": _percent(sector_cap),
            "liquidity_cap_percent": _percent(liquidity_cap),
            "final_sector_exposure_percent": _percent(final_sector_exposure),
            "estimated_single_trade_loss_percent": _percent(estimated_loss_percent),
        },
        "price_controls": {
            "reference_price": _money(reference),
            "stop_loss_price": _money(stop),
            "take_profit_price": _money(take),
            "stop_distance_percent": _percent(stop_distance_percent),
            "reward_risk_ratio": _percent(reward_risk_ratio),
        },
        "risk_checks": checks,
        "limits": {
            "max_single_trade_loss_percent": "2",
            "max_sector_exposure_percent": "30",
            "drawdown_reduction_trigger_percent": "15",
            "min_average_daily_turnover": "10000000",
            "max_turnover_participation_percent": "10",
            "min_reward_risk_ratio": "1.5",
        },
        "provenance": deepcopy(candidate["provenance"]),
        "execution": {
            "simulation_only": True,
            "simulation_execution_allowed": simulation_allowed,
            "order_created": False,
            "real_trading_allowed": False,
            "human_confirmation_required": human_required,
            "human_confirmed": context.human_confirmed,
        },
        "next_stage": "complete_financial_graph",
        "caveats": [
            "risk approval permits only a later simulation step; it does not create an order",
            "real trading is hard-disabled",
            "fixed project risk limits are educational controls, not personalized investment advice",
        ],
    }


def validate_risk_review(value: Any, query: RiskManagerQuery) -> CrossValidationResult:
    if not isinstance(value, Mapping):
        return CrossValidationResult(False, "risk review must be an object")
    try:
        expected = _build_risk_review(query)
    except (KeyError, RiskManagerError) as error:
        return CrossValidationResult(False, str(error))
    if dict(value) != expected:
        for field in expected:
            if value.get(field) != expected[field]:
                return CrossValidationResult(
                    False, f"risk review field does not match deterministic result: {field}"
                )
        return CrossValidationResult(False, "risk review does not match deterministic result")
    return CrossValidationResult(True)


class _RiskManagerAgent:
    name = "deterministic_risk_manager"

    def run(self, request: AgentRequest) -> AgentResponse:
        query = request.context.get("risk_query")
        if not isinstance(query, RiskManagerQuery):
            raise RiskManagerError("Risk Manager requires a RiskManagerQuery")
        review = _build_risk_review(query)
        return AgentResponse(
            content=f"risk decision={review['risk_decision']['status']}",
            metadata={"risk_review": review},
        )


class _RiskPreflightGuardrail:
    """Evaluate every hard risk rule before the Risk Manager Agent runs."""

    name = "risk_preflight"

    def check_input(self, request: AgentRequest) -> None:
        query = request.context.get("risk_query")
        if not isinstance(query, RiskManagerQuery):
            raise RiskManagerError("risk preflight requires a RiskManagerQuery")
        review = _build_risk_review(query)
        expected_checks = {
            "trader_candidate",
            "trading_session",
            "market_regime",
            "liquidity",
            "stop_loss_take_profit",
            "drawdown",
            "single_trade_loss",
            "sector_exposure",
            "human_confirmation",
            "real_trading_disabled",
        }
        actual_checks = {check["name"] for check in review["risk_checks"]}
        if actual_checks != expected_checks:
            raise RiskManagerError("risk preflight did not evaluate every C2 hard rule")
        if review["execution"]["real_trading_allowed"] is not False:
            raise RiskManagerError("risk preflight must keep real trading disabled")

    def check_output(self, response: AgentResponse) -> None:
        del response


@dataclass(frozen=True)
class RiskManagerResult:
    report: Mapping[str, Any]
    harness_trace: tuple[Mapping[str, Any], ...]
    trace: tuple[Mapping[str, Any], ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "report": deepcopy(dict(self.report)),
            "harness_trace": [deepcopy(dict(event)) for event in self.harness_trace],
            "trace": [deepcopy(dict(event)) for event in self.trace],
        }


class RiskManagerRuntime:
    """Deep risk seam enforcing every C2 hard rule before simulation."""

    def run(self, query: RiskManagerQuery) -> RiskManagerResult:
        if not isinstance(query, RiskManagerQuery):
            raise RiskManagerError("query must be a RiskManagerQuery")
        trace: list[dict[str, Any]] = [
            {
                "event": "risk_manager.started",
                "detail": f"symbol={query.trader_candidate['symbol']}",
            },
            {
                "event": "risk_manager.inputs.validated",
                "detail": "C1, Trader candidate, and risk context accepted",
            },
        ]
        harness = AgentHarness(
            _RiskManagerAgent(),
            guardrails=(
                _RiskPreflightGuardrail(),
                JSONSchemaValidator(
                    output_schema=RISK_REVIEW_SCHEMA,
                    output_path="metadata.risk_review",
                    name="risk_review_schema",
                ),
                CrossValidator(
                    lambda review: validate_risk_review(review, query),
                    output_path="metadata.risk_review",
                    name="risk_review_recompute",
                ),
            ),
        )
        harness_result = harness.run(
            AgentRequest(
                task="Apply deterministic C2 risk controls",
                context={"risk_query": query},
            )
        )
        report = harness_result.response.metadata["risk_review"]
        trace.extend(
            (
                {
                    "event": "risk_manager.checks.completed",
                    "detail": f"checks={len(report['risk_checks'])}",
                },
                {
                    "event": "risk_manager.decision.created",
                    "detail": f"status={report['risk_decision']['status']}",
                },
                {
                    "event": "risk_manager.completed",
                    "detail": "risk result passed Harness validation; no order created",
                },
            )
        )
        return RiskManagerResult(
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
        if not isinstance(state, Mapping):
            raise RiskManagerError("risk graph state must be an object")
        query = RiskManagerQuery(
            c1_decision=state.get("c1_decision", {}),
            trader_candidate=state.get("trader_candidate", {}),
            risk_context=RiskContext.from_mapping(state.get("risk_context", {})),
        )
        return {"risk_review": self.run(query).to_mapping()}


@dataclass(frozen=True)
class C2TradingQuery:
    c1_decision: Mapping[str, Any]
    risk_context: RiskContext

    def __post_init__(self) -> None:
        if not isinstance(self.risk_context, RiskContext):
            raise RiskManagerError("risk_context must be a RiskContext")
        trader_query = TraderQuery(self.c1_decision)
        object.__setattr__(self, "c1_decision", trader_query.c1_decision)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "C2TradingQuery":
        if not isinstance(value, Mapping) or "c1_decision" not in value:
            raise RiskManagerError("C2 graph state is missing c1_decision")
        return cls(
            c1_decision=value["c1_decision"],
            risk_context=RiskContext.from_mapping(value.get("risk_context", {})),
        )


@dataclass(frozen=True)
class C2TradingResult:
    report: Mapping[str, Any]
    trace: tuple[Mapping[str, Any], ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "report": deepcopy(dict(self.report)),
            "trace": [deepcopy(dict(event)) for event in self.trace],
        }


class C2TradingRuntime:
    """Deep C2 seam: C1 research to Trader candidate to Risk Manager review."""

    def __init__(
        self,
        *,
        trader_runtime: TraderRuntime,
        risk_manager_runtime: RiskManagerRuntime,
    ) -> None:
        self._trader_runtime = trader_runtime
        self._risk_manager_runtime = risk_manager_runtime

    def run(self, query: C2TradingQuery) -> C2TradingResult:
        if not isinstance(query, C2TradingQuery):
            raise RiskManagerError("query must be a C2TradingQuery")
        trace: list[dict[str, Any]] = [
            {"event": "c2.started", "detail": "Trader and Risk Manager pipeline started"}
        ]
        trader = self._trader_runtime.run(TraderQuery(query.c1_decision)).to_mapping()
        trace.append(
            {
                "event": "c2.trader.completed",
                "detail": f"action={trader['report']['signal']['action']}",
            }
        )
        risk = self._risk_manager_runtime.run(
            RiskManagerQuery(
                c1_decision=query.c1_decision,
                trader_candidate=trader,
                risk_context=query.risk_context,
            )
        ).to_mapping()
        trace.append(
            {
                "event": "c2.risk_manager.completed",
                "detail": f"status={risk['report']['risk_decision']['status']}",
            }
        )
        report = {
            "status": "c2_completed",
            "symbol": trader["report"]["symbol"],
            "mode": trader["report"]["mode"],
            "trader": trader,
            "risk_manager": risk,
            "simulation_only": True,
            "order_created": False,
            "real_trading_allowed": False,
            "next_stage": "complete_financial_graph",
        }
        trace.append(
            {
                "event": "c2.completed",
                "detail": "Trader candidate and deterministic risk review completed",
            }
        )
        return C2TradingResult(report=report, trace=tuple(trace))

    def run_graph_node(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"c2_trading": self.run(C2TradingQuery.from_mapping(state)).to_mapping()}


def build_default_risk_manager_runtime() -> RiskManagerRuntime:
    return RiskManagerRuntime()


def build_default_c2_trading_runtime() -> C2TradingRuntime:
    return C2TradingRuntime(
        trader_runtime=build_default_trader_runtime(),
        risk_manager_runtime=build_default_risk_manager_runtime(),
    )


__all__ = [
    "C2TradingQuery",
    "C2TradingResult",
    "C2TradingRuntime",
    "DRAWDOWN_REDUCTION_TRIGGER_PERCENT",
    "MAX_SECTOR_EXPOSURE_PERCENT",
    "MAX_SINGLE_TRADE_LOSS_PERCENT",
    "MIN_AVERAGE_DAILY_TURNOVER",
    "RISK_REVIEW_SCHEMA",
    "RiskContext",
    "RiskManagerError",
    "RiskManagerQuery",
    "RiskManagerResult",
    "RiskManagerRuntime",
    "build_default_c2_trading_runtime",
    "build_default_risk_manager_runtime",
    "validate_risk_review",
]
