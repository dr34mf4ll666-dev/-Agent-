"""Persistent, simulation-only execution for D4 paper trading sessions."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Mapping

from .financial_graph import validate_financial_graph_report


class PaperTradingError(ValueError):
    """A paper trading request or persisted ledger is invalid."""


def _aware_datetime(value: str | datetime, field: str) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise PaperTradingError(f"{field} must be an ISO datetime") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise PaperTradingError(f"{field} must include a timezone")
    return result


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PaperTradingError(f"{field} must be numeric") from error
    if not result.is_finite():
        raise PaperTradingError(f"{field} must be finite")
    return result


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _price(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001")))


@dataclass(frozen=True)
class PaperTradingSessionConfig:
    """Stable configuration for one resumable local simulation session."""

    session_id: str
    symbols: tuple[str, ...]
    initial_cash: Decimal
    started_at: datetime
    planned_end_at: datetime
    board_lot: int = 100
    commission_rate: Decimal = Decimal("0.0003")
    minimum_commission: Decimal = Decimal("5")
    stamp_duty_rate: Decimal = Decimal("0.0005")
    slippage_basis_points: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise PaperTradingError("session_id must not be empty")
        if not self.symbols or any(not item.strip() for item in self.symbols):
            raise PaperTradingError("symbols must contain at least one symbol")
        if self.initial_cash <= 0:
            raise PaperTradingError("initial_cash must be positive")
        _aware_datetime(self.started_at, "started_at")
        _aware_datetime(self.planned_end_at, "planned_end_at")
        planned_days = (self.planned_end_at.date() - self.started_at.date()).days
        if not 7 <= planned_days <= 14:
            raise PaperTradingError("planned paper run must cover 7 to 14 days")
        if self.board_lot <= 0:
            raise PaperTradingError("board_lot must be positive")
        for field in (
            "commission_rate",
            "minimum_commission",
            "stamp_duty_rate",
            "slippage_basis_points",
        ):
            if getattr(self, field) < 0:
                raise PaperTradingError(f"{field} must not be negative")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "symbols": list(self.symbols),
            "initial_cash": _money(self.initial_cash),
            "started_at": self.started_at.isoformat(),
            "planned_end_at": self.planned_end_at.isoformat(),
            "board_lot": self.board_lot,
            "commission_rate": str(self.commission_rate),
            "minimum_commission": str(self.minimum_commission),
            "stamp_duty_rate": str(self.stamp_duty_rate),
            "slippage_basis_points": str(self.slippage_basis_points),
            "simulation_only": True,
            "real_trading_allowed": False,
        }


@dataclass(frozen=True)
class PaperTradingQuote:
    """A separately sourced execution quote with explicit time semantics."""

    symbol: str
    price: Decimal
    source: str
    timestamp: datetime
    as_of: datetime
    mode: str

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise PaperTradingError("quote symbol must not be empty")
        if self.price <= 0:
            raise PaperTradingError("quote price must be positive")
        if not self.source.strip():
            raise PaperTradingError("quote source must not be empty")
        _aware_datetime(self.timestamp, "quote.timestamp")
        _aware_datetime(self.as_of, "quote.as_of")
        if self.mode not in {"offline", "live"}:
            raise PaperTradingError("quote mode must be offline or live")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PaperTradingQuote":
        if not isinstance(value, Mapping):
            raise PaperTradingError("quote must be an object")
        try:
            return cls(
                symbol=str(value["symbol"]),
                price=_decimal(value["price"], "quote.price"),
                source=str(value["source"]),
                timestamp=_aware_datetime(value["timestamp"], "quote.timestamp"),
                as_of=_aware_datetime(value["as_of"], "quote.as_of"),
                mode=str(value["mode"]),
            )
        except KeyError as error:
            raise PaperTradingError(f"quote is missing {error.args[0]}") from error

    @classmethod
    def from_financial_report(
        cls, report: Mapping[str, Any]
    ) -> "PaperTradingQuote":
        """Build the reproducible offline quote from C3 technical evidence."""
        try:
            technical = report["research"]["report"]["combined_analysis"][
                "reports"
            ]["technical"]
            return cls(
                symbol=str(report["symbol"]),
                price=_decimal(technical["latest_close"], "latest_close"),
                source=str(technical["sources"][0]),
                timestamp=_aware_datetime(technical["timestamp"], "timestamp"),
                as_of=_aware_datetime(technical["as_of"], "as_of"),
                mode=str(report["mode"]),
            )
        except (KeyError, IndexError, TypeError) as error:
            raise PaperTradingError("C3 report has no traceable technical quote") from error


@dataclass(frozen=True)
class PaperTradingCycleRequest:
    """One C3 decision evaluated against its point-in-time market quote."""

    cycle_id: str
    evaluated_at: datetime
    financial_report: Mapping[str, Any]
    quote: PaperTradingQuote
    confirmation_actor: str = "user"
    confirmation_note: str = ""

    def __post_init__(self) -> None:
        if not self.cycle_id.strip():
            raise PaperTradingError("cycle_id must not be empty")
        _aware_datetime(self.evaluated_at, "evaluated_at")
        if not isinstance(self.financial_report, Mapping):
            raise PaperTradingError("financial_report must be an object")
        if not isinstance(self.quote, PaperTradingQuote):
            raise PaperTradingError("quote must be a PaperTradingQuote")


@dataclass(frozen=True)
class PaperTradingCycleResult:
    status: str
    cycle: Mapping[str, Any]
    account: Mapping[str, Any]
    review: Mapping[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "cycle": deepcopy(dict(self.cycle)),
            "account": deepcopy(dict(self.account)),
            "review": deepcopy(dict(self.review)),
        }


class JsonPaperTradingLedger:
    """Owns the versioned one-file ledger and atomic persistence boundary."""

    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def open(self, config: PaperTradingSessionConfig) -> dict[str, Any]:
        if self.path.exists():
            ledger = self.load()
            if ledger["session"]["session_id"] != config.session_id:
                raise PaperTradingError("ledger belongs to another session_id")
            requested = config.to_mapping()
            for field in (
                "symbols",
                "initial_cash",
                "board_lot",
                "commission_rate",
                "minimum_commission",
                "stamp_duty_rate",
                "slippage_basis_points",
            ):
                if ledger["session"].get(field) != requested[field]:
                    raise PaperTradingError(
                        f"session configuration changed after start: {field}"
                    )
            return ledger
        now = config.started_at.isoformat()
        ledger = {
            "version": self.VERSION,
            "session": config.to_mapping(),
            "account": {
                "cash": _money(config.initial_cash),
                "positions": {},
                "realized_pnl": "0.00",
            },
            "cycles": [],
            "failures": [],
            "confirmations": [],
            "reviews": [],
            "created_at": now,
            "updated_at": now,
        }
        self.save(ledger)
        return ledger

    def load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PaperTradingError(f"cannot read paper ledger: {error}") from error
        if not isinstance(value, dict) or value.get("version") != self.VERSION:
            raise PaperTradingError("paper ledger version is unsupported")
        for field in ("session", "account", "cycles", "failures", "confirmations"):
            if field not in value:
                raise PaperTradingError(f"paper ledger is missing {field}")
        return value

    def save(self, ledger: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(ledger, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as error:
            raise PaperTradingError(f"cannot save paper ledger: {error}") from error
        finally:
            if temporary.exists():
                temporary.unlink()


class PaperTradingRuntime:
    """Turns validated C3 decisions into local simulated fills and one ledger."""

    def __init__(self, *, store: JsonPaperTradingLedger) -> None:
        self._store = store

    def start(self, config: PaperTradingSessionConfig) -> Mapping[str, Any]:
        return deepcopy(self._store.open(config))

    def run_cycle(
        self,
        config: PaperTradingSessionConfig,
        request: PaperTradingCycleRequest,
    ) -> PaperTradingCycleResult:
        ledger = self._store.open(config)
        if any(item["cycle_id"] == request.cycle_id for item in ledger["cycles"]):
            raise PaperTradingError(f"cycle_id already exists: {request.cycle_id}")
        try:
            cycle = self._execute(config, ledger, request)
        except Exception as error:
            failure = {
                "cycle_id": request.cycle_id,
                "recorded_at": request.evaluated_at.isoformat(),
                "error_type": type(error).__name__,
                "message": str(error),
                "recovered": False,
            }
            ledger["failures"].append(failure)
            ledger["updated_at"] = request.evaluated_at.isoformat()
            self._store.save(ledger)
            if isinstance(error, PaperTradingError):
                raise
            raise PaperTradingError(str(error)) from error

        ledger["cycles"].append(cycle)
        ledger["updated_at"] = request.evaluated_at.isoformat()
        review = self._build_review(ledger)
        ledger["reviews"].append(review)
        self._store.save(ledger)
        return PaperTradingCycleResult(
            status=cycle["status"],
            cycle=cycle,
            account=deepcopy(ledger["account"]),
            review=review,
        )

    def review(self) -> Mapping[str, Any]:
        return deepcopy(self._build_review(self._store.load()))

    def record_failure_recovery(
        self, *, cycle_id: str, recovered_at: str | datetime, note: str
    ) -> Mapping[str, Any]:
        ledger = self._store.load()
        recovered = _aware_datetime(recovered_at, "recovered_at")
        matches = [item for item in ledger["failures"] if item["cycle_id"] == cycle_id]
        if not matches:
            raise PaperTradingError(f"failure cycle_id does not exist: {cycle_id}")
        target = matches[-1]
        target.update(
            {"recovered": True, "recovered_at": recovered.isoformat(), "note": note}
        )
        ledger["updated_at"] = recovered.isoformat()
        self._store.save(ledger)
        return deepcopy(target)

    def _execute(
        self,
        config: PaperTradingSessionConfig,
        ledger: dict[str, Any],
        request: PaperTradingCycleRequest,
    ) -> dict[str, Any]:
        report = request.financial_report
        validation = validate_financial_graph_report(report)
        if not validation.valid:
            raise PaperTradingError(f"C3 report rejected: {validation.detail}")
        if report.get("simulation_only") is not True:
            raise PaperTradingError("C3 report must be simulation_only")
        if report.get("order_created") is not False:
            raise PaperTradingError("C3 must not create an order")
        if report.get("real_trading_allowed") is not False:
            raise PaperTradingError("real trading must remain disabled")

        symbol = str(report["symbol"])
        if symbol not in config.symbols:
            raise PaperTradingError(f"symbol is outside this session: {symbol}")
        quote = request.quote
        if quote.symbol != symbol:
            raise PaperTradingError("execution quote symbol does not match C3 report")
        if quote.mode != report["mode"]:
            raise PaperTradingError("execution quote mode does not match C3 report")
        if quote.as_of > request.evaluated_at:
            raise PaperTradingError("market quote as_of must not be in the future")

        risk = report.get("risk_manager")
        risk_report = risk.get("report") if isinstance(risk, Mapping) else None
        execution = risk_report.get("execution", {}) if risk_report else {}
        confirmation_required = bool(execution.get("human_confirmation_required"))
        human_confirmed = bool(execution.get("human_confirmed"))
        if confirmation_required:
            ledger["confirmations"].append(
                {
                    "cycle_id": request.cycle_id,
                    "actor": request.confirmation_actor,
                    "decision": "approved" if human_confirmed else "missing",
                    "note": request.confirmation_note,
                    "recorded_at": request.evaluated_at.isoformat(),
                }
            )

        action = str(report["final_decision"].get("approved_action", "hold"))
        target_percent = self._target_percent(report)
        simulation_allowed = bool(execution.get("simulation_execution_allowed"))
        order = None
        status = "no_action"
        if confirmation_required and not human_confirmed:
            status = "pending_human_confirmation"
        elif action in {"buy", "sell", "reduce"} and simulation_allowed:
            order = self._rebalance(
                config=config,
                account=ledger["account"],
                symbol=symbol,
                raw_price=quote.price,
                target_percent=target_percent,
                evaluated_at=request.evaluated_at,
                cycle_id=request.cycle_id,
            )
            status = "simulated_fill" if order else "no_rebalance_needed"

        return {
            "cycle_id": request.cycle_id,
            "status": status,
            "evaluated_at": request.evaluated_at.isoformat(),
            "symbol": symbol,
            "mode": report["mode"],
            "decision": {
                "status": report["final_decision"].get("status"),
                "approved_action": action,
                "target_position_percent": str(target_percent),
                "reason": report["final_decision"].get("reason"),
                "decision_source": report.get("decision_source"),
            },
            "c3_snapshot": self._c3_snapshot(report),
            "quote": {
                "close": _price(quote.price),
                "source": quote.source,
                "timestamp": quote.timestamp.isoformat(),
                "as_of": quote.as_of.isoformat(),
            },
            "confirmation_required": confirmation_required,
            "human_confirmed": human_confirmed,
            "simulated_order": order,
            "safety": {
                "simulation_only": True,
                "order_sent_to_broker": False,
                "real_trading_allowed": False,
            },
        }

    @staticmethod
    def _c3_snapshot(report: Mapping[str, Any]) -> dict[str, Any]:
        research = report["research"]["report"]
        synthesis = research["synthesis"]
        risk = report.get("risk_manager")
        risk_report = risk.get("report") if isinstance(risk, Mapping) else None
        return {
            "status": report["status"],
            "route": report["route"]["selected_path"],
            "research_inclination": synthesis["inclination"],
            "research_confidence": synthesis["confidence"],
            "market_regime": synthesis["market_regime_gate"]["regime"],
            "risk_status": (
                risk_report["risk_decision"]["status"] if risk_report else "skipped"
            ),
            "estimated_single_trade_loss_percent": (
                risk_report["position"]["estimated_single_trade_loss_percent"]
                if risk_report
                else None
            ),
            "sources": list(research["sources"]),
        }

    @staticmethod
    def _target_percent(report: Mapping[str, Any]) -> Decimal:
        risk = report.get("risk_manager")
        if isinstance(risk, Mapping):
            value = risk["report"]["position"]["approved_percent"]
        else:
            value = report["final_decision"].get("approved_position_percent", "0")
        result = _decimal(value, "approved position percent")
        if result < 0 or result > 100:
            raise PaperTradingError("approved position percent must be between 0 and 100")
        return result

    @staticmethod
    def _rebalance(
        *,
        config: PaperTradingSessionConfig,
        account: dict[str, Any],
        symbol: str,
        raw_price: Decimal,
        target_percent: Decimal,
        evaluated_at: datetime,
        cycle_id: str,
    ) -> dict[str, Any] | None:
        cash = _decimal(account["cash"], "cash")
        positions = account["positions"]
        position = positions.get(
            symbol,
            {"shares": 0, "average_cost": "0.0000", "last_price": _price(raw_price)},
        )
        shares = int(position["shares"])
        average_cost = _decimal(position["average_cost"], "average_cost")
        equity = cash + sum(
            _decimal(item["last_price"], "last_price") * Decimal(int(item["shares"]))
            for item in positions.values()
        )
        target_value = equity * target_percent / Decimal("100")
        desired = int((target_value / raw_price).to_integral_value(rounding=ROUND_DOWN))
        desired = desired // config.board_lot * config.board_lot
        delta = desired - shares
        if delta == 0:
            position["last_price"] = _price(raw_price)
            positions[symbol] = position
            return None

        side = "buy" if delta > 0 else "sell"
        quantity = abs(delta)
        slippage_rate = config.slippage_basis_points / Decimal("10000")
        execution_price = raw_price * (
            Decimal("1") + slippage_rate if side == "buy" else Decimal("1") - slippage_rate
        )
        if side == "buy":
            while quantity > 0:
                gross = execution_price * Decimal(quantity)
                commission = max(gross * config.commission_rate, config.minimum_commission)
                if gross + commission <= cash:
                    break
                quantity -= config.board_lot
            if quantity <= 0:
                raise PaperTradingError("simulated order rejected: insufficient cash")
            gross = execution_price * Decimal(quantity)
            commission = max(gross * config.commission_rate, config.minimum_commission)
            stamp_duty = Decimal("0")
            new_shares = shares + quantity
            total_cost = gross + commission
            new_average = (
                average_cost * Decimal(shares) + total_cost
            ) / Decimal(new_shares)
            cash -= total_cost
            realized = Decimal("0")
        else:
            quantity = min(quantity, shares)
            if quantity <= 0:
                return None
            gross = execution_price * Decimal(quantity)
            commission = max(gross * config.commission_rate, config.minimum_commission)
            stamp_duty = gross * config.stamp_duty_rate
            proceeds = gross - commission - stamp_duty
            realized = proceeds - average_cost * Decimal(quantity)
            cash += proceeds
            new_shares = shares - quantity
            new_average = average_cost if new_shares else Decimal("0")

        slippage = abs(execution_price - raw_price) * Decimal(quantity)
        account["cash"] = _money(cash)
        account["realized_pnl"] = _money(
            _decimal(account["realized_pnl"], "realized_pnl") + realized
        )
        positions[symbol] = {
            "shares": new_shares,
            "average_cost": _price(new_average),
            "last_price": _price(raw_price),
        }
        return {
            "order_id": f"paper-{cycle_id}",
            "side": side,
            "quantity": quantity,
            "raw_price": _price(raw_price),
            "execution_price": _price(execution_price),
            "gross_amount": _money(gross),
            "commission": _money(commission),
            "stamp_duty": _money(stamp_duty),
            "slippage_cost": _money(slippage),
            "filled_at": evaluated_at.isoformat(),
            "broker": "local_simulator",
            "real_order": False,
        }

    @staticmethod
    def _build_review(ledger: Mapping[str, Any]) -> dict[str, Any]:
        cycles = ledger["cycles"]
        live_dates = sorted(
            {
                _aware_datetime(item["quote"]["as_of"], "quote.as_of").date()
                for item in cycles
                if item.get("mode") == "live"
            }
        )
        coverage_days = (
            (live_dates[-1] - live_dates[0]).days + 1 if live_dates else 0
        )
        completed = sum(
            item["status"]
            in {"simulated_fill", "no_rebalance_needed", "no_action"}
            for item in cycles
        )
        duration_met = coverage_days >= 7 and len(live_dates) >= 5
        return {
            "generated_at": ledger["updated_at"],
            "session_id": ledger["session"]["session_id"],
            "started_at": ledger["session"]["started_at"],
            "planned_end_at": ledger["session"]["planned_end_at"],
            "cycle_count": len(cycles),
            "completed_cycle_count": completed,
            "failure_count": len(ledger["failures"]),
            "pending_confirmation_count": sum(
                item["status"] == "pending_human_confirmation" for item in cycles
            ),
            "confirmation_record_count": len(ledger["confirmations"]),
            "simulated_fill_count": sum(
                item["status"] == "simulated_fill" for item in cycles
            ),
            "live_trading_dates": [item.isoformat() for item in live_dates],
            "live_trading_day_count": len(live_dates),
            "live_calendar_coverage_days": coverage_days,
            "duration_requirement_met": duration_met,
            "duration_rule": "at least 7 calendar days and 5 distinct live market dates",
            "formal_task_status": "done" if duration_met else "in_progress",
            "account": deepcopy(dict(ledger["account"])),
            "latest_cycle": deepcopy(cycles[-1]) if cycles else None,
            "latest_failure": (
                deepcopy(ledger["failures"][-1]) if ledger["failures"] else None
            ),
            "latest_confirmation": (
                deepcopy(ledger["confirmations"][-1])
                if ledger["confirmations"]
                else None
            ),
        }


__all__ = [
    "JsonPaperTradingLedger",
    "PaperTradingCycleRequest",
    "PaperTradingCycleResult",
    "PaperTradingError",
    "PaperTradingQuote",
    "PaperTradingRuntime",
    "PaperTradingSessionConfig",
]
