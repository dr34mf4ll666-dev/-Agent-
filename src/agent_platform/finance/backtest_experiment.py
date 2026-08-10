"""Point-in-time C3 signal adapter and reproducible multi-symbol backtests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestError,
    BacktestRequest,
    BacktestSignal,
    CorporateAction,
    TradingSessionConstraint,
)
from .contracts import MarketDataSeries
from .financial_graph import validate_financial_graph_report


class BacktestExperimentError(ValueError):
    """A point-in-time signal or fixed experiment is invalid."""


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise BacktestExperimentError(f"{field} must be decimal-compatible")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise BacktestExperimentError(
            f"{field} must be decimal-compatible"
        ) from error
    if not parsed.is_finite():
        raise BacktestExperimentError(f"{field} must be finite")
    return parsed


def _aware(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise BacktestExperimentError(f"{field} must be ISO 8601") from error
    else:
        raise BacktestExperimentError(f"{field} must be ISO 8601")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BacktestExperimentError(f"{field} must include a timezone")
    return parsed


def _query_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise BacktestExperimentError(f"{field} must use YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as error:
        raise BacktestExperimentError(f"{field} must use YYYYMMDD") from error


@dataclass(frozen=True)
class PointInTimeEvidence:
    """One fact used by C3, including when it became available."""

    name: str
    source: str
    as_of: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        name = self.name.strip() if isinstance(self.name, str) else ""
        source = self.source.strip() if isinstance(self.source, str) else ""
        if not name or not source:
            raise BacktestExperimentError("evidence name and source must be non-empty")
        for field in ("as_of", "available_at"):
            value = getattr(self, field)
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise BacktestExperimentError(
                    f"evidence {field} must include a timezone"
                )
        if self.as_of > self.available_at:
            raise BacktestExperimentError(
                "evidence cannot be available before the period it describes"
            )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "source", source)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PointInTimeEvidence":
        try:
            return cls(
                name=value["name"],
                source=value["source"],
                as_of=_aware(value["as_of"], "evidence.as_of"),
                available_at=_aware(
                    value["available_at"],
                    "evidence.available_at",
                ),
            )
        except KeyError as error:
            raise BacktestExperimentError(
                f"evidence is missing {error.args[0]}"
            ) from error


@dataclass(frozen=True)
class C3DecisionSnapshot:
    """A C3 report frozen at one historical decision time."""

    signal_at: datetime
    generated_at: datetime
    report: Mapping[str, Any]
    evidence: tuple[PointInTimeEvidence, ...]

    def __init__(
        self,
        *,
        signal_at: datetime,
        generated_at: datetime,
        report: Mapping[str, Any],
        evidence: Sequence[PointInTimeEvidence],
    ) -> None:
        if not isinstance(report, Mapping):
            raise BacktestExperimentError("C3 report must be an object")
        normalized_evidence = tuple(evidence)
        if not normalized_evidence or any(
            not isinstance(item, PointInTimeEvidence)
            for item in normalized_evidence
        ):
            raise BacktestExperimentError(
                "C3 snapshot requires point-in-time evidence"
            )
        signal_at = _aware(signal_at, "signal_at")
        generated_at = _aware(generated_at, "generated_at")
        if generated_at < signal_at:
            raise BacktestExperimentError(
                "C3 generated_at must not be earlier than signal_at"
            )
        if any(item.as_of > signal_at for item in normalized_evidence):
            raise BacktestExperimentError(
                "C3 evidence as_of is later than the completed signal bar"
            )
        if any(item.available_at > generated_at for item in normalized_evidence):
            raise BacktestExperimentError(
                "C3 used evidence that was not available when the report was generated"
            )
        if report.get("status") != "financial_graph_completed":
            raise BacktestExperimentError("C3 report status must be completed")
        validation = validate_financial_graph_report(report)
        if not validation.valid:
            raise BacktestExperimentError(
                f"C3 report failed cross-validation: {validation.detail}"
            )
        for field, expected in (
            ("simulation_only", True),
            ("order_created", False),
            ("real_trading_allowed", False),
        ):
            if report.get(field) is not expected:
                raise BacktestExperimentError(
                    f"C3 safety field changed: {field}"
                )
        object.__setattr__(self, "signal_at", signal_at)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "report", MappingProxyType(dict(report)))
        object.__setattr__(self, "evidence", normalized_evidence)


class C3BacktestSignalAdapter:
    """Convert audited C3 reports into target-position backtest signals."""

    def convert(
        self,
        snapshots: Sequence[C3DecisionSnapshot],
    ) -> tuple[BacktestSignal, ...]:
        normalized = tuple(snapshots)
        if not normalized:
            return ()
        if any(not isinstance(item, C3DecisionSnapshot) for item in normalized):
            raise BacktestExperimentError("snapshots must contain C3DecisionSnapshot")
        symbols = {str(item.report.get("symbol", "")).lower() for item in normalized}
        if "" in symbols or len(symbols) != 1:
            raise BacktestExperimentError("C3 snapshots must contain one symbol")
        if any(
            current.signal_at >= following.signal_at
            for current, following in zip(normalized, normalized[1:])
        ):
            raise BacktestExperimentError(
                "C3 snapshot signal times must be strictly increasing"
            )
        return tuple(self._convert_one(item) for item in normalized)

    @staticmethod
    def _convert_one(snapshot: C3DecisionSnapshot) -> BacktestSignal:
        report = snapshot.report
        symbol = str(report["symbol"]).lower()
        decision = report.get("final_decision")
        if not isinstance(decision, Mapping):
            raise BacktestExperimentError("C3 report is missing final_decision")
        action = str(decision.get("approved_action", "")).lower()
        if action not in {"buy", "sell", "hold"}:
            raise BacktestExperimentError("C3 approved_action is invalid")
        position: Any = None
        risk = report.get("risk_manager")
        if isinstance(risk, Mapping):
            try:
                position = risk["report"]["position"]["approved_percent"]
            except (KeyError, TypeError) as error:
                raise BacktestExperimentError(
                    "C3 Risk Manager output is missing approved position"
                ) from error
        if position is None:
            position = decision.get("approved_position_percent")
        if position is None and action == "sell":
            position = "0"
        if position is None:
            raise BacktestExperimentError(
                "C3 final decision is missing target position"
            )
        target = _decimal(position, "approved_position_percent")
        sources = sorted({item.source for item in snapshot.evidence})
        return BacktestSignal(
            symbol=symbol,
            signal_at=snapshot.signal_at,
            available_at=snapshot.generated_at,
            target_position_percent=target,
            source="c3.financial_graph:" + ",".join(sources),
            rationale=(
                f"C3 approved {action}; point-in-time evidence count="
                f"{len(snapshot.evidence)}"
            ),
        )


@dataclass(frozen=True)
class BacktestExperimentConfig:
    """Frozen stock pool, period, benchmark, costs, and acceptance baseline."""

    name: str
    symbols: tuple[str, ...]
    benchmark_symbol: str
    start_date: date
    end_date: date
    initial_cash: Decimal
    sharpe_baseline: Decimal
    execution: BacktestConfig
    signal_policy: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BacktestExperimentConfig":
        try:
            symbols = tuple(str(item).lower() for item in value["symbols"])
            execution = value["execution"]
            config = cls(
                name=str(value["name"]).strip(),
                symbols=symbols,
                benchmark_symbol=str(value["benchmark_symbol"]).lower(),
                start_date=_query_date(value["start_date"], "start_date"),
                end_date=_query_date(value["end_date"], "end_date"),
                initial_cash=_decimal(value["initial_cash"], "initial_cash"),
                sharpe_baseline=_decimal(
                    value.get("sharpe_baseline", "0.5"),
                    "sharpe_baseline",
                ),
                execution=BacktestConfig(
                    initial_cash=Decimal("1"),
                    commission_rate=execution["commission_rate"],
                    minimum_commission=execution["minimum_commission"],
                    stamp_duty_rate=execution["stamp_duty_rate"],
                    slippage_basis_points=execution["slippage_basis_points"],
                    board_lot=execution["board_lot"],
                    annual_trading_days=execution["annual_trading_days"],
                ),
                signal_policy=MappingProxyType(dict(value["signal_policy"])),
            )
        except (KeyError, TypeError) as error:
            raise BacktestExperimentError(
                f"experiment config is missing or invalid: {error}"
            ) from error
        config._validate()
        return config

    @classmethod
    def load(cls, path: str | Path) -> "BacktestExperimentConfig":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BacktestExperimentError("experiment config is unreadable") from error
        if not isinstance(value, Mapping):
            raise BacktestExperimentError("experiment config must be an object")
        return cls.from_mapping(value)

    def _validate(self) -> None:
        if not self.name:
            raise BacktestExperimentError("experiment name must be non-empty")
        if not self.symbols or len(self.symbols) != len(set(self.symbols)):
            raise BacktestExperimentError("experiment symbols must be non-empty and unique")
        if self.start_date > self.end_date:
            raise BacktestExperimentError("experiment start_date must not exceed end_date")
        if self.initial_cash <= 0:
            raise BacktestExperimentError("experiment initial_cash must be positive")
        if self.sharpe_baseline < 0:
            raise BacktestExperimentError("sharpe_baseline must not be negative")
        required_policy = {
            "kind",
            "warmup_bars",
            "rebalance_every_bars",
            "short_window",
            "long_window",
            "positive_target_percent",
            "negative_target_percent",
        }
        if required_policy.difference(self.signal_policy):
            raise BacktestExperimentError("signal_policy is incomplete")
        if not isinstance(self.signal_policy["kind"], str) or not self.signal_policy[
            "kind"
        ].strip():
            raise BacktestExperimentError("signal_policy.kind must be non-empty")
        for field in (
            "warmup_bars",
            "rebalance_every_bars",
            "short_window",
            "long_window",
        ):
            value = self.signal_policy[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise BacktestExperimentError(
                    f"signal_policy.{field} must be a positive integer"
                )
        if self.signal_policy["short_window"] >= self.signal_policy["long_window"]:
            raise BacktestExperimentError(
                "signal_policy short_window must be less than long_window"
            )
        for field in ("positive_target_percent", "negative_target_percent"):
            target = _decimal(self.signal_policy[field], f"signal_policy.{field}")
            if not 0 <= target <= 100:
                raise BacktestExperimentError(
                    f"signal_policy.{field} must be from 0 to 100"
                )

    def to_mapping(self) -> dict[str, Any]:
        execution = self.execution.to_mapping()
        execution.pop("initial_cash")
        return {
            "name": self.name,
            "symbols": list(self.symbols),
            "benchmark_symbol": self.benchmark_symbol,
            "start_date": self.start_date.strftime("%Y%m%d"),
            "end_date": self.end_date.strftime("%Y%m%d"),
            "initial_cash": str(self.initial_cash),
            "sharpe_baseline": str(self.sharpe_baseline),
            "execution": execution,
            "signal_policy": dict(self.signal_policy),
        }


@dataclass(frozen=True)
class BacktestExperimentResult:
    report: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "report", MappingProxyType(dict(self.report)))

    def to_mapping(self) -> dict[str, Any]:
        return dict(self.report)


class BacktestExperimentRunner:
    """Run an equal-cash fixed pool through the same deterministic engine."""

    def __init__(self, signal_adapter: C3BacktestSignalAdapter | None = None) -> None:
        self._signal_adapter = signal_adapter or C3BacktestSignalAdapter()

    def run(
        self,
        *,
        config: BacktestExperimentConfig,
        market_data: Mapping[str, MarketDataSeries],
        decisions: Mapping[str, Sequence[C3DecisionSnapshot]],
        benchmark: MarketDataSeries,
        trading_constraints: Mapping[
            str, Sequence[TradingSessionConstraint]
        ] | None = None,
        corporate_actions: Mapping[str, Sequence[CorporateAction]] | None = None,
    ) -> BacktestExperimentResult:
        if not isinstance(config, BacktestExperimentConfig):
            raise BacktestExperimentError("config must be BacktestExperimentConfig")
        expected = set(config.symbols)
        if set(market_data) != expected or set(decisions) != expected:
            raise BacktestExperimentError(
                "market_data and decisions must exactly match the fixed stock pool"
            )
        if benchmark.symbol != config.benchmark_symbol:
            raise BacktestExperimentError("benchmark symbol does not match config")
        constraints = trading_constraints or {}
        actions = corporate_actions or {}
        allocation = config.initial_cash / Decimal(len(config.symbols))
        reports: dict[str, Mapping[str, Any]] = {}
        point_maps: list[dict[str, Decimal]] = []
        total_cost = Decimal("0")
        total_commission = Decimal("0")
        total_stamp_duty = Decimal("0")
        total_slippage = Decimal("0")
        total_orders = 0
        total_signals = 0
        for symbol in config.symbols:
            series = market_data[symbol]
            if series.symbol != symbol:
                raise BacktestExperimentError("market series symbol does not match key")
            self._validate_period(series, config)
            if not decisions[symbol]:
                raise BacktestExperimentError(
                    "every fixed-pool symbol requires at least one Agent decision"
                )
            signals = self._signal_adapter.convert(decisions[symbol])
            result = BacktestEngine().run(
                BacktestRequest(
                    series=series,
                    signals=signals,
                    config=BacktestConfig(
                        initial_cash=allocation,
                        commission_rate=config.execution.commission_rate,
                        minimum_commission=config.execution.minimum_commission,
                        stamp_duty_rate=config.execution.stamp_duty_rate,
                        slippage_basis_points=config.execution.slippage_basis_points,
                        board_lot=config.execution.board_lot,
                        annual_trading_days=config.execution.annual_trading_days,
                    ),
                    trading_constraints=tuple(constraints.get(symbol, ())),
                    corporate_actions=tuple(actions.get(symbol, ())),
                )
            ).to_mapping()
            reports[symbol] = result
            point_maps.append(
                {
                    item["as_of"]: Decimal(item["equity"])
                    for item in result["equity_curve"]
                }
            )
            total_cost += Decimal(result["costs"]["total_cny"])
            total_commission += Decimal(result["costs"]["commission_cny"])
            total_stamp_duty += Decimal(result["costs"]["stamp_duty_cny"])
            total_slippage += Decimal(result["costs"]["slippage_cny"])
            total_orders += result["executed_order_count"]
            total_signals += result["signal_count"]

        common_times = sorted(set.intersection(*(set(item) for item in point_maps)))
        if len(common_times) < 2:
            raise BacktestExperimentError(
                "fixed pool needs at least two aligned trading dates"
            )
        portfolio_curve = [
            {
                "as_of": as_of,
                "equity": str(sum((item[as_of] for item in point_maps), Decimal("0"))),
            }
            for as_of in common_times
        ]
        portfolio_metrics = self._portfolio_metrics(
            [Decimal(item["equity"]) for item in portfolio_curve],
            config,
        )
        benchmark_metrics = self._benchmark_metrics(benchmark, config)
        portfolio_metrics["excess_return_vs_benchmark_percent"] = str(
            (
                Decimal(portfolio_metrics["total_return_percent"])
                - Decimal(benchmark_metrics["total_return_percent"])
            ).quantize(Decimal("0.0001"))
        )
        observed_sharpe = portfolio_metrics["annualized_sharpe"]
        baseline_met = (
            observed_sharpe is not None
            and Decimal(observed_sharpe) > config.sharpe_baseline
        )
        report = {
            "status": "fixed_backtest_experiment_completed",
            "config": config.to_mapping(),
            "data": {
                "market_sources": sorted(
                    {
                        bar.source
                        for series in market_data.values()
                        for bar in series.bars
                    }
                ),
                "bar_counts": {
                    symbol: len(market_data[symbol].bars)
                    for symbol in config.symbols
                },
                "point_in_time_c3_verified": True,
            },
            "signal_count": total_signals,
            "executed_order_count": total_orders,
            "costs": {
                "commission_cny": str(total_commission.quantize(Decimal("0.01"))),
                "stamp_duty_cny": str(total_stamp_duty.quantize(Decimal("0.01"))),
                "slippage_cny": str(total_slippage.quantize(Decimal("0.01"))),
                "total_cny": str(total_cost.quantize(Decimal("0.01"))),
            },
            "portfolio_metrics": portfolio_metrics,
            "benchmark": benchmark_metrics,
            "sharpe_baseline": {
                "operator": ">",
                "target": str(config.sharpe_baseline),
                "observed": observed_sharpe,
                "met": baseline_met,
                "interpretation": (
                    "baseline met on the fixed experiment"
                    if baseline_met
                    else "baseline not met; result is reported without tuning on future data"
                ),
            },
            "per_symbol": reports,
            "portfolio_equity_curve": portfolio_curve,
            "simulation_only": True,
            "order_created": False,
            "real_trading_allowed": False,
        }
        return BacktestExperimentResult(report)

    @staticmethod
    def _validate_period(
        series: MarketDataSeries,
        config: BacktestExperimentConfig,
    ) -> None:
        first = series.bars[0].as_of.date()
        last = series.bars[-1].as_of.date()
        if first != config.start_date or last != config.end_date:
            raise BacktestExperimentError(
                "market series must exactly match the fixed experiment period"
            )

    @staticmethod
    def _portfolio_metrics(
        equities: list[Decimal],
        config: BacktestExperimentConfig,
    ) -> dict[str, Any]:
        total_return = (equities[-1] / equities[0] - 1) * 100
        peak = equities[0]
        max_drawdown = Decimal("0")
        for equity in equities:
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        returns = [
            equities[index] / equities[index - 1] - 1
            for index in range(1, len(equities))
        ]
        sharpe = None
        if len(returns) >= 2:
            mean = sum(returns, Decimal("0")) / Decimal(len(returns))
            variance = sum((item - mean) ** 2 for item in returns) / Decimal(
                len(returns) - 1
            )
            if variance > 0:
                sharpe = (
                    mean
                    / variance.sqrt()
                    * Decimal(config.execution.annual_trading_days).sqrt()
                )
        return {
            "initial_equity": str(equities[0].quantize(Decimal("0.01"))),
            "final_equity": str(equities[-1].quantize(Decimal("0.01"))),
            "total_return_percent": str(total_return.quantize(Decimal("0.0001"))),
            "max_drawdown_percent": str(max_drawdown.quantize(Decimal("0.0001"))),
            "annualized_sharpe": (
                str(sharpe.quantize(Decimal("0.0001")))
                if sharpe is not None
                else None
            ),
        }

    @staticmethod
    def _benchmark_metrics(
        benchmark: MarketDataSeries,
        config: BacktestExperimentConfig,
    ) -> dict[str, Any]:
        BacktestExperimentRunner._validate_period(benchmark, config)
        first = benchmark.bars[0].close
        last = benchmark.bars[-1].close
        return {
            "symbol": benchmark.symbol,
            "start_close": str(first),
            "end_close": str(last),
            "total_return_percent": str(
                ((last / first - 1) * 100).quantize(Decimal("0.0001"))
            ),
            "source": benchmark.bars[0].source,
        }


__all__ = [
    "BacktestExperimentConfig",
    "BacktestExperimentError",
    "BacktestExperimentResult",
    "BacktestExperimentRunner",
    "C3BacktestSignalAdapter",
    "C3DecisionSnapshot",
    "PointInTimeEvidence",
]
