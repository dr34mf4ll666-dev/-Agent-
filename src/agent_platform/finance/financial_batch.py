"""C3 batch orchestration over the single-symbol financial Graph interface."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .financial_graph import FinancialGraphQuery, FinancialGraphRuntime


class FinancialBatchError(ValueError):
    """The batch request is structurally invalid."""


@dataclass(frozen=True)
class FinancialBatchQuery:
    queries: tuple[FinancialGraphQuery, ...]

    def __init__(self, queries: Sequence[FinancialGraphQuery]) -> None:
        normalized = tuple(queries)
        if not normalized:
            raise FinancialBatchError("batch must contain at least one query")
        if any(not isinstance(query, FinancialGraphQuery) for query in normalized):
            raise FinancialBatchError("batch queries must be FinancialGraphQuery values")
        symbols = [
            query.c1_query.combined_query.technical.symbol for query in normalized
        ]
        if len(symbols) != len(set(symbols)):
            raise FinancialBatchError("batch symbols must be unique")
        object.__setattr__(self, "queries", normalized)


@dataclass(frozen=True)
class FinancialBatchResult:
    status: str
    requested_count: int
    completed_count: int
    failed_count: int
    acceptance_20_met: bool
    reports: tuple[Mapping[str, Any], ...]
    trade_advice: tuple[Mapping[str, Any], ...]
    audit_logs: tuple[Mapping[str, Any], ...]
    failures: tuple[Mapping[str, Any], ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "requested_count": self.requested_count,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "acceptance_20_met": self.acceptance_20_met,
            "reports": [deepcopy(dict(item)) for item in self.reports],
            "trade_advice": [deepcopy(dict(item)) for item in self.trade_advice],
            "audit_logs": [deepcopy(dict(item)) for item in self.audit_logs],
            "failures": [deepcopy(dict(item)) for item in self.failures],
        }


ProgressCallback = Callable[[int, int, str, str], None]
RuntimeFactory = Callable[[], FinancialGraphRuntime]


def _trade_advice(report: Mapping[str, Any]) -> dict[str, Any]:
    research = report["research"]["report"]
    synthesis = research["synthesis"]
    trader = report["trader"]["report"]
    decision = report["final_decision"]
    risk = report.get("risk_manager")
    if isinstance(risk, Mapping):
        position = risk["report"]["position"]
        approved_position = position["approved_percent"]
        estimated_loss = position["estimated_single_trade_loss_percent"]
    else:
        approved_position = decision.get("approved_position_percent", "0")
        estimated_loss = "0"
    return {
        "symbol": report["symbol"],
        "mode": report["mode"],
        "research_inclination": synthesis["inclination"],
        "confidence": synthesis["confidence"],
        "target_price_interval": deepcopy(dict(synthesis["target_price_interval"])),
        "candidate_action": trader["signal"]["action"],
        "route": report["route"]["selected_path"],
        "decision_status": decision["status"],
        "approved_action": decision["approved_action"],
        "approved_position_percent": approved_position,
        "estimated_single_trade_loss_percent": estimated_loss,
        "simulation_only": True,
        "order_created": False,
        "real_trading_allowed": False,
    }


def _audit_log(report: Mapping[str, Any], graph: Mapping[str, Any]) -> dict[str, Any]:
    research = report["research"]
    combined = research["report"]["combined_analysis"]
    risk = report.get("risk_manager")
    return {
        "symbol": report["symbol"],
        "mode": report["mode"],
        "sources": list(combined["sources"]),
        "specialist_times": {
            name: {
                "timestamp": specialist["timestamp"],
                "as_of": specialist["as_of"],
            }
            for name, specialist in combined["reports"].items()
        },
        "graph": deepcopy(dict(graph)),
        "specialist_harness_trace": {
            name: deepcopy(loop["harness_trace"])
            for name, loop in combined["loops"].items()
        },
        "c1_trace": deepcopy(research.get("trace", [])),
        "trader_harness_trace": deepcopy(
            report["trader"].get("harness_trace", [])
        ),
        "risk_manager_harness_trace": (
            deepcopy(risk.get("harness_trace", []))
            if isinstance(risk, Mapping)
            else []
        ),
        "simulation_only": True,
        "order_created": False,
        "real_trading_allowed": False,
    }


class FinancialBatchRuntime:
    """Run isolated one-symbol Graphs and return reports, advice, and audits."""

    def __init__(
        self,
        runtime_factory: RuntimeFactory,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        if not callable(runtime_factory):
            raise FinancialBatchError("runtime_factory must be callable")
        self._runtime_factory = runtime_factory
        self._progress_callback = progress_callback

    def run(self, query: FinancialBatchQuery) -> FinancialBatchResult:
        if not isinstance(query, FinancialBatchQuery):
            raise FinancialBatchError("query must be a FinancialBatchQuery")
        total = len(query.queries)
        reports: list[Mapping[str, Any]] = []
        advice: list[Mapping[str, Any]] = []
        audits: list[Mapping[str, Any]] = []
        failures: list[Mapping[str, Any]] = []
        for index, item in enumerate(query.queries, start=1):
            symbol = item.c1_query.combined_query.technical.symbol
            self._notify(index, total, symbol, "started")
            try:
                result = self._runtime_factory().run(item).to_mapping()
                report = result["report"]
                reports.append(report)
                advice.append(_trade_advice(report))
                audits.append(_audit_log(report, result["graph"]))
            except Exception as error:  # one provider failure must not hide other stocks
                failures.append(
                    {
                        "symbol": symbol,
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                )
                self._notify(index, total, symbol, "failed")
                continue
            self._notify(index, total, symbol, "completed")
        completed = len(reports)
        failed = len(failures)
        acceptance = total >= 20 and completed == total and failed == 0
        return FinancialBatchResult(
            status=(
                "batch_completed" if failed == 0 else "batch_completed_with_failures"
            ),
            requested_count=total,
            completed_count=completed,
            failed_count=failed,
            acceptance_20_met=acceptance,
            reports=tuple(reports),
            trade_advice=tuple(advice),
            audit_logs=tuple(audits),
            failures=tuple(failures),
        )

    def _notify(self, index: int, total: int, symbol: str, status: str) -> None:
        if self._progress_callback is not None:
            self._progress_callback(index, total, symbol, status)


__all__ = [
    "FinancialBatchError",
    "FinancialBatchQuery",
    "FinancialBatchResult",
    "FinancialBatchRuntime",
]
