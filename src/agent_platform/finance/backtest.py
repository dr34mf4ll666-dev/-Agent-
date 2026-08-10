"""Deterministic D1 backtest with explicit signal and execution time semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import MarketBar, MarketDataSeries


class BacktestError(ValueError):
    """The backtest request or deterministic accounting is invalid."""


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise BacktestError(f"{field_name} must be a decimal number") from error
    if not parsed.is_finite():
        raise BacktestError(f"{field_name} must be finite")
    return parsed


def _aware_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise BacktestError(f"{field_name} must be ISO 8601") from error
    else:
        raise BacktestError(f"{field_name} must be ISO 8601")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BacktestError(f"{field_name} must include a timezone")
    return parsed


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _number(value: Decimal, places: str = "0.0000") -> str:
    return str(value.quantize(Decimal(places)))


@dataclass(frozen=True)
class BacktestSignal:
    """A target position decided after one completed market bar."""

    symbol: str
    signal_at: datetime
    target_position_percent: Decimal
    source: str
    rationale: str = ""
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().lower() if isinstance(self.symbol, str) else ""
        source = self.source.strip() if isinstance(self.source, str) else ""
        if not symbol:
            raise BacktestError("signal symbol must be non-empty")
        if not source:
            raise BacktestError("signal source must be non-empty")
        if not isinstance(self.signal_at, datetime):
            raise BacktestError("signal_at must be a datetime")
        if self.signal_at.tzinfo is None or self.signal_at.utcoffset() is None:
            raise BacktestError("signal_at must include a timezone")
        available_at = self.available_at or self.signal_at
        if not isinstance(available_at, datetime):
            raise BacktestError("available_at must be a datetime")
        if available_at.tzinfo is None or available_at.utcoffset() is None:
            raise BacktestError("available_at must include a timezone")
        if available_at < self.signal_at:
            raise BacktestError("available_at must not be earlier than signal_at")
        target = _decimal(
            self.target_position_percent,
            "target_position_percent",
        )
        if not Decimal("0") <= target <= Decimal("100"):
            raise BacktestError("target_position_percent must be from 0 to 100")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "target_position_percent", target)
        object.__setattr__(self, "available_at", available_at)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BacktestSignal":
        try:
            return cls(
                symbol=value["symbol"],
                signal_at=_aware_datetime(value["signal_at"], "signal_at"),
                target_position_percent=_decimal(
                    value["target_position_percent"],
                    "target_position_percent",
                ),
                source=value["source"],
                rationale=str(value.get("rationale", "")),
                available_at=(
                    _aware_datetime(value["available_at"], "available_at")
                    if value.get("available_at") is not None
                    else None
                ),
            )
        except KeyError as error:
            raise BacktestError(
                f"signal is missing required field: {error.args[0]}"
            ) from error


@dataclass(frozen=True)
class BacktestConfig:
    """Fixed and serializable assumptions for one reproducible experiment."""

    initial_cash: Decimal = Decimal("100000")
    commission_rate: Decimal = Decimal("0.0003")
    minimum_commission: Decimal = Decimal("5")
    stamp_duty_rate: Decimal = Decimal("0.0005")
    slippage_basis_points: Decimal = Decimal("5")
    board_lot: int = 100
    annual_trading_days: int = 252

    def __post_init__(self) -> None:
        for name in (
            "initial_cash",
            "commission_rate",
            "minimum_commission",
            "stamp_duty_rate",
            "slippage_basis_points",
        ):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        if self.initial_cash <= 0:
            raise BacktestError("initial_cash must be positive")
        for name in (
            "commission_rate",
            "minimum_commission",
            "stamp_duty_rate",
            "slippage_basis_points",
        ):
            if getattr(self, name) < 0:
                raise BacktestError(f"{name} must not be negative")
        if (
            isinstance(self.board_lot, bool)
            or not isinstance(self.board_lot, int)
            or self.board_lot < 1
        ):
            raise BacktestError("board_lot must be a positive integer")
        if (
            isinstance(self.annual_trading_days, bool)
            or not isinstance(self.annual_trading_days, int)
            or self.annual_trading_days < 1
        ):
            raise BacktestError("annual_trading_days must be a positive integer")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "initial_cash": str(self.initial_cash),
            "commission_rate": str(self.commission_rate),
            "minimum_commission": str(self.minimum_commission),
            "stamp_duty_rate": str(self.stamp_duty_rate),
            "slippage_basis_points": str(self.slippage_basis_points),
            "board_lot": self.board_lot,
            "annual_trading_days": self.annual_trading_days,
        }


@dataclass(frozen=True)
class TradingSessionConstraint:
    """Point-in-time exchange constraint for one symbol and trading day."""

    symbol: str
    as_of: datetime
    buy_allowed: bool
    sell_allowed: bool
    reason: str
    source: str
    timestamp: datetime
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().lower() if isinstance(self.symbol, str) else ""
        source = self.source.strip() if isinstance(self.source, str) else ""
        if not symbol:
            raise BacktestError("constraint symbol must be non-empty")
        if not source:
            raise BacktestError("constraint source must be non-empty")
        if not isinstance(self.buy_allowed, bool) or not isinstance(
            self.sell_allowed, bool
        ):
            raise BacktestError("constraint permissions must be booleans")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise BacktestError("constraint reason must be non-empty")
        for name in ("as_of", "timestamp"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise BacktestError(f"constraint {name} must include a timezone")
        if self.as_of > self.timestamp:
            raise BacktestError("constraint as_of must not be later than timestamp")
        available_at = self.available_at or self.as_of.replace(
            hour=9,
            minute=30,
            second=0,
            microsecond=0,
        )
        if not isinstance(available_at, datetime) or available_at.tzinfo is None:
            raise BacktestError("constraint available_at must include a timezone")
        if available_at > self.as_of:
            raise BacktestError("constraint available_at must not be later than as_of")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "reason", self.reason.strip())
        object.__setattr__(self, "available_at", available_at)


@dataclass(frozen=True)
class CorporateAction:
    """Known cash/share adjustment applied on an ex-right or ex-dividend day."""

    symbol: str
    as_of: datetime
    announced_at: datetime
    cash_dividend_per_share: Decimal = Decimal("0")
    share_multiplier: Decimal = Decimal("1")
    source: str = ""
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().lower() if isinstance(self.symbol, str) else ""
        source = self.source.strip() if isinstance(self.source, str) else ""
        if not symbol:
            raise BacktestError("corporate action symbol must be non-empty")
        if not source:
            raise BacktestError("corporate action source must be non-empty")
        for name in ("as_of", "announced_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise BacktestError(f"corporate action {name} must include a timezone")
        timestamp = self.timestamp or self.as_of
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise BacktestError("corporate action timestamp must include a timezone")
        if self.announced_at > self.as_of:
            raise BacktestError("corporate action must be announced by its effective day")
        if self.as_of > timestamp:
            raise BacktestError("corporate action as_of must not be later than timestamp")
        dividend = _decimal(
            self.cash_dividend_per_share,
            "cash_dividend_per_share",
        )
        multiplier = _decimal(self.share_multiplier, "share_multiplier")
        if dividend < 0:
            raise BacktestError("cash_dividend_per_share must not be negative")
        if multiplier <= 0:
            raise BacktestError("share_multiplier must be positive")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "cash_dividend_per_share", dividend)
        object.__setattr__(self, "share_multiplier", multiplier)


@dataclass(frozen=True)
class BacktestRequest:
    series: MarketDataSeries
    signals: tuple[BacktestSignal, ...]
    config: BacktestConfig = BacktestConfig()
    trading_constraints: tuple[TradingSessionConstraint, ...] = ()
    corporate_actions: tuple[CorporateAction, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.series, MarketDataSeries):
            raise BacktestError("series must be a MarketDataSeries")
        if not isinstance(self.config, BacktestConfig):
            raise BacktestError("config must be a BacktestConfig")
        signals = tuple(self.signals)
        if any(not isinstance(signal, BacktestSignal) for signal in signals):
            raise BacktestError("signals must contain BacktestSignal values")
        if any(signal.symbol != self.series.symbol for signal in signals):
            raise BacktestError("signal symbol must match market data symbol")
        if any(
            current.signal_at >= following.signal_at
            for current, following in zip(signals, signals[1:])
        ):
            raise BacktestError("signal_at values must be strictly increasing")
        bar_times = {bar.as_of for bar in self.series.bars}
        if any(signal.signal_at not in bar_times for signal in signals):
            raise BacktestError(
                "every signal_at must match a completed market bar as_of"
            )
        constraints = tuple(self.trading_constraints)
        actions = tuple(self.corporate_actions)
        self._validate_events(
            constraints,
            expected_type=TradingSessionConstraint,
            label="trading_constraints",
            bar_times=bar_times,
        )
        bars_by_time = {bar.as_of: bar for bar in self.series.bars}
        if any(
            constraint.available_at > _execution_time(bars_by_time[constraint.as_of])
            for constraint in constraints
        ):
            raise BacktestError(
                "trading constraint must be available by the execution time"
            )
        if any(
            action.announced_at > _execution_time(bars_by_time[action.as_of])
            for action in actions
        ):
            raise BacktestError(
                "corporate action must be announced before its effective open"
            )
        self._validate_events(
            actions,
            expected_type=CorporateAction,
            label="corporate_actions",
            bar_times=bar_times,
        )
        object.__setattr__(self, "signals", signals)
        object.__setattr__(self, "trading_constraints", constraints)
        object.__setattr__(self, "corporate_actions", actions)

    def _validate_events(
        self,
        values: tuple[Any, ...],
        *,
        expected_type: type,
        label: str,
        bar_times: set[datetime],
    ) -> None:
        if any(not isinstance(value, expected_type) for value in values):
            raise BacktestError(f"{label} contains an invalid value")
        if any(value.symbol != self.series.symbol for value in values):
            raise BacktestError(f"{label} symbol must match market data symbol")
        event_times = [value.as_of for value in values]
        if len(event_times) != len(set(event_times)):
            raise BacktestError(f"{label} must contain at most one event per bar")
        if any(value.as_of not in bar_times for value in values):
            raise BacktestError(f"{label} as_of must match a market bar")


@dataclass(frozen=True)
class BacktestResult:
    report: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "report", MappingProxyType(dict(self.report)))

    def to_mapping(self) -> dict[str, Any]:
        return dict(self.report)


class BacktestEngine:
    """Run one long-only A-share simulation behind a single interface."""

    def run(self, request: BacktestRequest) -> BacktestResult:
        if not isinstance(request, BacktestRequest):
            raise BacktestError("request must be a BacktestRequest")
        config = request.config
        cash = config.initial_cash
        shares = 0
        average_cost = Decimal("0")
        pending: BacktestSignal | None = None
        signals = {signal.signal_at: signal for signal in request.signals}
        orders: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        realized_pnls: list[Decimal] = []
        total_commission = Decimal("0")
        total_stamp_duty = Decimal("0")
        total_slippage = Decimal("0")
        total_cash_dividends = Decimal("0")
        constraints = {
            constraint.as_of: constraint
            for constraint in request.trading_constraints
        }
        corporate_actions = {
            action.as_of: action for action in request.corporate_actions
        }
        applied_actions: list[dict[str, Any]] = []
        blocked_executions: list[dict[str, Any]] = []

        for bar in request.series.bars:
            action = corporate_actions.get(bar.as_of)
            if action is not None:
                shares_before = shares
                exact_shares = Decimal(shares) * action.share_multiplier
                if exact_shares != exact_shares.to_integral_value():
                    raise BacktestError(
                        "corporate action produced fractional shares; provide a cash-in-lieu adjustment"
                    )
                dividend_cash = Decimal(shares) * action.cash_dividend_per_share
                shares = int(exact_shares)
                cash += dividend_cash
                if shares_before:
                    adjusted_cost = max(
                        average_cost - action.cash_dividend_per_share,
                        Decimal("0"),
                    )
                    average_cost = adjusted_cost / action.share_multiplier
                total_cash_dividends += dividend_cash
                applied = {
                    "as_of": bar.as_of.isoformat(),
                    "announced_at": action.announced_at.isoformat(),
                    "cash_dividend_per_share": str(
                        action.cash_dividend_per_share
                    ),
                    "share_multiplier": str(action.share_multiplier),
                    "cash_received": _money(dividend_cash),
                    "shares_before": shares_before,
                    "shares_after": shares,
                    "source": action.source,
                }
                applied_actions.append(applied)
                trace.append({"event": "corporate_action.applied", **applied})

            if pending is not None and pending.signal_at < bar.as_of:
                if pending.available_at > _execution_time(bar):
                    blocked = {
                        "event": "execution.skipped.signal_not_available",
                        "signal_at": pending.signal_at.isoformat(),
                        "available_at": pending.available_at.isoformat(),
                        "bar_as_of": bar.as_of.isoformat(),
                        "reason": "signal was generated after this bar opened",
                    }
                    trace.append(blocked)
                    blocked_executions.append(blocked)
                elif bar.volume == 0:
                    blocked = {
                        "event": "execution.skipped.suspended",
                        "signal_at": pending.signal_at.isoformat(),
                        "bar_as_of": bar.as_of.isoformat(),
                        "reason": "zero volume",
                    }
                    trace.append(blocked)
                    blocked_executions.append(blocked)
                else:
                    side = self._target_side(
                        bar=bar,
                        signal=pending,
                        cash=cash,
                        shares=shares,
                        config=config,
                    )
                    constraint = constraints.get(bar.as_of)
                    permission_denied = (
                        constraint is not None
                        and (
                            (side == "buy" and not constraint.buy_allowed)
                            or (side == "sell" and not constraint.sell_allowed)
                        )
                    )
                    if permission_denied:
                        blocked = {
                            "event": "execution.skipped.market_constraint",
                            "signal_at": pending.signal_at.isoformat(),
                            "bar_as_of": bar.as_of.isoformat(),
                            "side": side,
                            "reason": constraint.reason,
                            "source": constraint.source,
                        }
                        trace.append(blocked)
                        blocked_executions.append(blocked)
                    else:
                        execution = self._rebalance(
                            bar=bar,
                            signal=pending,
                            cash=cash,
                            shares=shares,
                            average_cost=average_cost,
                            config=config,
                        )
                        cash = execution["cash"]
                        shares = execution["shares"]
                        average_cost = execution["average_cost"]
                        if execution["order"] is not None:
                            order = execution["order"]
                            orders.append(order)
                            total_commission += execution["commission"]
                            total_stamp_duty += execution["stamp_duty"]
                            total_slippage += execution["slippage"]
                            if execution["realized_pnl"] is not None:
                                realized_pnls.append(execution["realized_pnl"])
                        trace.append(execution["trace"])
                        pending = None

            equity = cash + Decimal(shares) * bar.close
            equity_curve.append(
                {
                    "as_of": bar.as_of.isoformat(),
                    "cash": _money(cash),
                    "shares": shares,
                    "close": str(bar.close),
                    "equity": _money(equity),
                }
            )

            signal = signals.get(bar.as_of)
            if signal is not None:
                if pending is not None:
                    trace.append(
                        {
                            "event": "signal.replaced",
                            "signal_at": signal.signal_at.isoformat(),
                            "detail": "newer signal replaced an unexecuted signal",
                        }
                    )
                pending = signal
                trace.append(
                    {
                        "event": "signal.recorded",
                        "signal_at": signal.signal_at.isoformat(),
                        "target_position_percent": str(
                            signal.target_position_percent
                        ),
                        "source": signal.source,
                    }
                )

        final_equity = cash + Decimal(shares) * request.series.bars[-1].close
        metrics = self._metrics(
            initial_cash=config.initial_cash,
            equity_curve=equity_curve,
            realized_pnls=realized_pnls,
            annual_trading_days=config.annual_trading_days,
        )
        report = {
            "status": "backtest_completed",
            "symbol": request.series.symbol,
            "period": {
                "start": request.series.bars[0].as_of.isoformat(),
                "end": request.series.bars[-1].as_of.isoformat(),
                "bar_count": len(request.series.bars),
            },
            "market_data": {
                "sources": sorted({bar.source for bar in request.series.bars}),
                "latest_timestamp": max(
                    bar.timestamp for bar in request.series.bars
                ).isoformat(),
                "price_adjustment": "raw_prices_with_explicit_corporate_actions",
            },
            "time_semantics": {
                "signal": "after completed daily bar close",
                "execution": "next tradable daily bar open at 09:30 Asia/Shanghai",
                "same_bar_execution_allowed": False,
                "execution_layer_uses_future_data": False,
                "signal_generation_verified_no_future": False,
                "signal_generation_status": "precomputed_input_not_yet_rolling_verified",
            },
            "config": config.to_mapping(),
            "signal_count": len(request.signals),
            "executed_order_count": len(orders),
            "pending_signal": (
                {
                    "signal_at": pending.signal_at.isoformat(),
                    "available_at": pending.available_at.isoformat(),
                    "target_position_percent": str(
                        pending.target_position_percent
                    ),
                    "reason": "no later tradable bar",
                }
                if pending is not None
                else None
            ),
            "orders": orders,
            "equity_curve": equity_curve,
            "costs": {
                "commission_cny": _money(total_commission),
                "stamp_duty_cny": _money(total_stamp_duty),
                "slippage_cny": _money(total_slippage),
                "total_cny": _money(
                    total_commission + total_stamp_duty + total_slippage
                ),
            },
            "market_constraints": {
                "provided_count": len(request.trading_constraints),
                "blocked_execution_count": len(blocked_executions),
                "blocked_executions": blocked_executions,
            },
            "corporate_actions": {
                "provided_count": len(request.corporate_actions),
                "applied_count": len(applied_actions),
                "cash_dividends_cny": _money(total_cash_dividends),
                "events": applied_actions,
            },
            "metrics": metrics,
            "final_portfolio": {
                "cash": _money(cash),
                "shares": shares,
                "market_value": _money(
                    Decimal(shares) * request.series.bars[-1].close
                ),
                "equity": _money(final_equity),
            },
            "trace": trace,
            "simulation_only": True,
            "order_created": False,
            "real_trading_allowed": False,
            "limitations": [
                "execution prices are raw; dividends and share changes require explicit corporate-action records",
                "zero volume is treated as suspension; exact exchange status can be supplied as a trading constraint",
                "price-limit and other exchange restrictions require point-in-time buy/sell permissions",
                "signals are precomputed inputs; rolling C3 signal generation is not connected yet",
                "one scripted trade cannot establish strategy quality or the Sharpe > 0.5 baseline",
            ],
        }
        return BacktestResult(report)

    @staticmethod
    def _rebalance(
        *,
        bar: MarketBar,
        signal: BacktestSignal,
        cash: Decimal,
        shares: int,
        average_cost: Decimal,
        config: BacktestConfig,
    ) -> dict[str, Any]:
        raw_price = bar.open
        desired_shares = BacktestEngine._desired_shares(
            raw_price=raw_price,
            target_position_percent=signal.target_position_percent,
            cash=cash,
            shares=shares,
            board_lot=config.board_lot,
        )
        delta = desired_shares - shares
        if delta == 0:
            return {
                "cash": cash,
                "shares": shares,
                "average_cost": average_cost,
                "commission": Decimal("0"),
                "stamp_duty": Decimal("0"),
                "slippage": Decimal("0"),
                "realized_pnl": None,
                "order": None,
                "trace": {
                    "event": "execution.no_rebalance_needed",
                    "signal_at": signal.signal_at.isoformat(),
                    "execution_at": _execution_time(bar).isoformat(),
                },
            }

        slippage_rate = config.slippage_basis_points / Decimal("10000")
        side = "buy" if delta > 0 else "sell"
        quantity = abs(delta)
        execution_price = (
            raw_price * (Decimal("1") + slippage_rate)
            if side == "buy"
            else raw_price * (Decimal("1") - slippage_rate)
        )

        if side == "buy":
            while quantity > 0:
                gross = execution_price * Decimal(quantity)
                commission = max(
                    gross * config.commission_rate,
                    config.minimum_commission,
                )
                if gross + commission <= cash:
                    break
                quantity -= config.board_lot
            if quantity <= 0:
                return {
                    "cash": cash,
                    "shares": shares,
                    "average_cost": average_cost,
                    "commission": Decimal("0"),
                    "stamp_duty": Decimal("0"),
                    "slippage": Decimal("0"),
                    "realized_pnl": None,
                    "order": None,
                    "trace": {
                        "event": "execution.rejected.insufficient_cash",
                        "signal_at": signal.signal_at.isoformat(),
                        "execution_at": _execution_time(bar).isoformat(),
                    },
                }
            gross = execution_price * Decimal(quantity)
            commission = max(
                gross * config.commission_rate,
                config.minimum_commission,
            )
            total_cost = gross + commission
            new_shares = shares + quantity
            new_average_cost = (
                average_cost * Decimal(shares) + total_cost
            ) / Decimal(new_shares)
            cash_after = cash - total_cost
            stamp_duty = Decimal("0")
            realized_pnl = None
        else:
            quantity = min(quantity, shares)
            gross = execution_price * Decimal(quantity)
            commission = max(
                gross * config.commission_rate,
                config.minimum_commission,
            )
            stamp_duty = gross * config.stamp_duty_rate
            net_proceeds = gross - commission - stamp_duty
            realized_pnl = net_proceeds - average_cost * Decimal(quantity)
            cash_after = cash + net_proceeds
            new_shares = shares - quantity
            new_average_cost = average_cost if new_shares else Decimal("0")

        slippage = abs(execution_price - raw_price) * Decimal(quantity)
        order = {
            "side": side,
            "signal_at": signal.signal_at.isoformat(),
            "signal_available_at": signal.available_at.isoformat(),
            "execution_at": _execution_time(bar).isoformat(),
            "price_field": "next_bar_open",
            "raw_open": str(raw_price),
            "execution_price": _number(execution_price, "0.0000"),
            "quantity": quantity,
            "gross_amount": _money(gross),
            "commission": _money(commission),
            "stamp_duty": _money(stamp_duty),
            "slippage_cost": _money(slippage),
            "cash_after": _money(cash_after),
            "shares_after": new_shares,
            "realized_pnl": (
                _money(realized_pnl) if realized_pnl is not None else None
            ),
            "signal_source": signal.source,
        }
        return {
            "cash": cash_after,
            "shares": new_shares,
            "average_cost": new_average_cost,
            "commission": commission,
            "stamp_duty": stamp_duty,
            "slippage": slippage,
            "realized_pnl": realized_pnl,
            "order": order,
            "trace": {
                "event": "execution.completed",
                "side": side,
                "signal_at": signal.signal_at.isoformat(),
                "execution_at": _execution_time(bar).isoformat(),
            },
        }

    @staticmethod
    def _desired_shares(
        *,
        raw_price: Decimal,
        target_position_percent: Decimal,
        cash: Decimal,
        shares: int,
        board_lot: int,
    ) -> int:
        equity_at_open = cash + Decimal(shares) * raw_price
        target_value = equity_at_open * target_position_percent / Decimal("100")
        desired = int(
            (target_value / raw_price).to_integral_value(rounding=ROUND_FLOOR)
        )
        return desired // board_lot * board_lot

    @staticmethod
    def _target_side(
        *,
        bar: MarketBar,
        signal: BacktestSignal,
        cash: Decimal,
        shares: int,
        config: BacktestConfig,
    ) -> str | None:
        desired = BacktestEngine._desired_shares(
            raw_price=bar.open,
            target_position_percent=signal.target_position_percent,
            cash=cash,
            shares=shares,
            board_lot=config.board_lot,
        )
        if desired > shares:
            return "buy"
        if desired < shares:
            return "sell"
        return None

    @staticmethod
    def _metrics(
        *,
        initial_cash: Decimal,
        equity_curve: list[Mapping[str, Any]],
        realized_pnls: list[Decimal],
        annual_trading_days: int,
    ) -> dict[str, Any]:
        equities = [Decimal(str(point["equity"])) for point in equity_curve]
        final_equity = equities[-1]
        total_return = (final_equity / initial_cash - Decimal("1")) * Decimal(
            "100"
        )
        peak = initial_cash
        max_drawdown = Decimal("0")
        for equity in equities:
            peak = max(peak, equity)
            if peak:
                drawdown = (peak - equity) / peak * Decimal("100")
                max_drawdown = max(max_drawdown, drawdown)

        daily_returns = [
            equities[index] / equities[index - 1] - Decimal("1")
            for index in range(1, len(equities))
            if equities[index - 1] != 0
        ]
        sharpe: Decimal | None = None
        if len(daily_returns) >= 2:
            mean = sum(daily_returns, Decimal("0")) / Decimal(
                len(daily_returns)
            )
            variance = sum(
                (value - mean) ** 2 for value in daily_returns
            ) / Decimal(len(daily_returns) - 1)
            if variance > 0:
                sharpe = (
                    mean
                    / variance.sqrt()
                    * Decimal(annual_trading_days).sqrt()
                )

        wins = [pnl for pnl in realized_pnls if pnl > 0]
        losses = [pnl for pnl in realized_pnls if pnl < 0]
        win_rate = (
            Decimal(len(wins)) / Decimal(len(realized_pnls)) * Decimal("100")
            if realized_pnls
            else None
        )
        profit_loss_ratio = None
        if wins and losses:
            average_win = sum(wins, Decimal("0")) / Decimal(len(wins))
            average_loss = abs(
                sum(losses, Decimal("0")) / Decimal(len(losses))
            )
            if average_loss:
                profit_loss_ratio = average_win / average_loss
        return {
            "initial_equity": _money(initial_cash),
            "final_equity": _money(final_equity),
            "total_return_percent": _number(total_return),
            "max_drawdown_percent": _number(max_drawdown),
            "annualized_sharpe": (
                _number(sharpe) if sharpe is not None else None
            ),
            "closed_trade_count": len(realized_pnls),
            "win_rate_percent": (
                _number(win_rate) if win_rate is not None else None
            ),
            "profit_loss_ratio": (
                _number(profit_loss_ratio)
                if profit_loss_ratio is not None
                else None
            ),
        }


def _execution_time(bar: MarketBar) -> datetime:
    return bar.as_of.replace(hour=9, minute=30, second=0, microsecond=0)


__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestError",
    "BacktestRequest",
    "BacktestResult",
    "BacktestSignal",
    "CorporateAction",
    "TradingSessionConstraint",
]
