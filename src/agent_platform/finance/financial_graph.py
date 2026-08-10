"""C3 single-symbol financial Graph orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_platform.core import CrossValidationResult, JsonCheckpointStore
from agent_platform.core.graph import GraphDefinition, GraphEdge, GraphResult, GraphRunner

from .c1_decision import (
    BEARISH_REGIMES,
    C1DecisionQuery,
    C1DecisionRuntime,
    build_default_c1_decision_runtime,
)
from .data_hub import FinancialDataPolicy
from .risk_manager import RiskContext, RiskManagerRuntime, build_default_risk_manager_runtime
from .trader import TraderRuntime, build_default_trader_runtime


class FinancialGraphError(ValueError):
    """The financial Graph query or deterministic result is invalid."""


def _object_schema(*required: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(required),
        "additionalProperties": True,
    }


def _plain_state(state: Mapping[str, Any]) -> Mapping[str, Any]:
    to_dict = getattr(state, "to_dict", None)
    return to_dict() if callable(to_dict) else state


@dataclass(frozen=True)
class FinancialGraphQuery:
    """Small interface for one complete single-symbol research and risk run."""

    c1_query: C1DecisionQuery
    risk_context: RiskContext

    def __post_init__(self) -> None:
        if not isinstance(self.c1_query, C1DecisionQuery):
            raise FinancialGraphError("c1_query must be a C1DecisionQuery")
        if not isinstance(self.risk_context, RiskContext):
            raise FinancialGraphError("risk_context must be a RiskContext")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FinancialGraphQuery":
        if not isinstance(value, Mapping):
            raise FinancialGraphError("financial Graph query must be an object")
        try:
            return cls(
                c1_query=C1DecisionQuery.from_mapping(value["c1_query"]),
                risk_context=RiskContext.from_mapping(value["risk_context"]),
            )
        except KeyError as error:
            raise FinancialGraphError(
                f"financial Graph query is missing {error.args[0]}"
            ) from error

    def to_mapping(self) -> dict[str, Any]:
        return {
            "c1_query": self.c1_query.to_mapping(),
            "risk_context": self.risk_context.to_mapping(),
        }


@dataclass(frozen=True)
class FinancialGraphResult:
    report: Mapping[str, Any]
    graph: Mapping[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "report": deepcopy(dict(self.report)),
            "graph": deepcopy(dict(self.graph)),
        }


def _route_after_trader(state: Mapping[str, Any]) -> dict[str, Any]:
    candidate = state["trader_candidate"]["report"]
    interval = candidate["target_price_interval"]
    risk_context = dict(state["risk_context"])
    stop_source = "request"
    take_source = "request"
    if risk_context.get("stop_loss_price") is None:
        risk_context["stop_loss_price"] = interval["lower"]
        stop_source = "c1_target_lower"
    if risk_context.get("take_profit_price") is None:
        risk_context["take_profit_price"] = interval["upper"]
        take_source = "c1_target_upper"
    action = str(candidate["signal"]["action"])
    regime = str(candidate["market_context"]["regime"]).lower()
    skip_bearish_buy = action == "buy" and regime in BEARISH_REGIMES
    selected_path = "skip_bearish_buy" if skip_bearish_buy else "risk_review"
    reason = (
        "bearish market regime blocks a new buy before Risk Manager execution"
        if skip_bearish_buy
        else "candidate requires deterministic Risk Manager review"
    )
    return {
        "market_route": {
            "selected_path": selected_path,
            "action": action,
            "regime": regime,
            "reason": reason,
            "stop_loss_source": stop_source,
            "take_profit_source": take_source,
        },
        "effective_risk_context": risk_context,
    }


def _build_market_skip(state: Mapping[str, Any]) -> dict[str, Any]:
    candidate = state["trader_candidate"]["report"]
    risk_context = RiskContext.from_mapping(state["effective_risk_context"])
    return {
        "market_skip": {
            "status": "blocked",
            "approved_action": "hold",
            "approved_position_percent": str(risk_context.current_position_percent),
            "reason": state["market_route"]["reason"],
            "symbol": candidate["symbol"],
            "mode": candidate["mode"],
            "simulation_allowed": False,
            "simulation_only": True,
            "order_created": False,
            "real_trading_allowed": False,
        }
    }


def _finalize_report(state: Mapping[str, Any]) -> dict[str, Any]:
    c1 = state["c1_decision"]
    trader = state["trader_candidate"]
    route = state["market_route"]
    risk = state.get("risk_review")
    market_skip = state.get("market_skip")
    if route["selected_path"] == "risk_review":
        final_decision = risk["report"]["risk_decision"]
        decision_source = "risk_manager"
    else:
        final_decision = market_skip
        decision_source = "market_route"
    return {
        "financial_report": {
            "status": "financial_graph_completed",
            "symbol": c1["report"]["symbol"],
            "mode": c1["report"]["mode"],
            "research": c1,
            "trader": trader,
            "route": deepcopy(dict(route)),
            "risk_manager": risk,
            "final_decision": deepcopy(dict(final_decision)),
            "decision_source": decision_source,
            "simulation_only": True,
            "order_created": False,
            "real_trading_allowed": False,
            "next_stage": "c3_checkpoint_and_batch",
        }
    }


def validate_financial_graph_report(value: Any) -> CrossValidationResult:
    if not isinstance(value, Mapping):
        return CrossValidationResult(False, "financial Graph report must be an object")
    try:
        if value.get("status") != "financial_graph_completed":
            raise FinancialGraphError("financial Graph status must be completed")
        c1 = value["research"]["report"]
        trader = value["trader"]["report"]
        route = value["route"]
        action = str(trader["signal"]["action"])
        regime = str(trader["market_context"]["regime"]).lower()
        expected_path = (
            "skip_bearish_buy"
            if action == "buy" and regime in BEARISH_REGIMES
            else "risk_review"
        )
        if value["symbol"] != c1["symbol"] or trader["symbol"] != c1["symbol"]:
            raise FinancialGraphError("C1 and Trader symbols do not match")
        if value["mode"] != c1["mode"] or trader["mode"] != c1["mode"]:
            raise FinancialGraphError("C1 and Trader modes do not match")
        if route.get("selected_path") != expected_path:
            raise FinancialGraphError("market conditional route was tampered")
        if expected_path == "risk_review":
            risk = value.get("risk_manager")
            if not isinstance(risk, Mapping):
                raise FinancialGraphError("risk review route requires Risk Manager output")
            if value.get("decision_source") != "risk_manager":
                raise FinancialGraphError("risk review route has invalid decision source")
            if value["final_decision"] != risk["report"]["risk_decision"]:
                raise FinancialGraphError("final decision does not match Risk Manager")
        else:
            if value.get("risk_manager") is not None:
                raise FinancialGraphError("bearish skip route must not run Risk Manager")
            decision = value["final_decision"]
            if decision.get("status") != "blocked" or decision.get(
                "approved_action"
            ) != "hold":
                raise FinancialGraphError("bearish buy must become a blocked hold")
            if value.get("decision_source") != "market_route":
                raise FinancialGraphError("bearish skip route has invalid decision source")
        for field, expected in (
            ("simulation_only", True),
            ("order_created", False),
            ("real_trading_allowed", False),
        ):
            if value.get(field) is not expected:
                raise FinancialGraphError(f"financial Graph safety field changed: {field}")
    except (KeyError, TypeError, FinancialGraphError) as error:
        return CrossValidationResult(False, str(error))
    return CrossValidationResult(True, "financial Graph report is internally consistent")


class FinancialGraphRuntime:
    """Deep C3 seam for one-symbol research, routing, and risk review."""

    def __init__(
        self,
        *,
        c1_runtime: C1DecisionRuntime,
        trader_runtime: TraderRuntime,
        risk_manager_runtime: RiskManagerRuntime,
        checkpoint_store: JsonCheckpointStore | None = None,
    ) -> None:
        self._c1_runtime = c1_runtime
        self._trader_runtime = trader_runtime
        self._risk_manager_runtime = risk_manager_runtime
        self._graph = self._build_graph()
        self._runner = GraphRunner(
            self._graph,
            checkpoint_store=checkpoint_store,
        )

    def _build_graph(self) -> GraphDefinition:
        c1_output = _object_schema("c1_decision")
        c1_input = _object_schema("c1_decision", "risk_context")
        trader_output = _object_schema("trader_candidate")
        trader_input = _object_schema("c1_decision", "trader_candidate", "risk_context")
        route_output = _object_schema("market_route", "effective_risk_context")
        route_input = _object_schema(
            "c1_decision",
            "trader_candidate",
            "effective_risk_context",
            "market_route",
        )
        risk_output = _object_schema("risk_review")
        risk_input = _object_schema("risk_review", "market_route")
        skip_output = _object_schema("market_skip")
        skip_input = _object_schema("market_skip", "market_route")
        return GraphDefinition(
            start="c1_research",
            nodes={
                "c1_research": self._run_c1_node,
                "trader": self._run_trader_node,
                "market_route": _route_after_trader,
                "risk_manager": self._run_risk_node,
                "market_bearish_skip": _build_market_skip,
                "finalize": _finalize_report,
            },
            edges=(
                GraphEdge(
                    "c1_research",
                    "trader",
                    output_schema=c1_output,
                    input_schema=c1_input,
                ),
                GraphEdge(
                    "trader",
                    "market_route",
                    output_schema=trader_output,
                    input_schema=trader_input,
                ),
                GraphEdge(
                    "market_route",
                    "risk_manager",
                    condition=lambda state: state["market_route"]["selected_path"]
                    == "risk_review",
                    output_schema=route_output,
                    input_schema=route_input,
                    condition_label="selected_path == risk_review",
                ),
                GraphEdge(
                    "market_route",
                    "market_bearish_skip",
                    condition=lambda state: state["market_route"]["selected_path"]
                    == "skip_bearish_buy",
                    output_schema=route_output,
                    input_schema=route_input,
                    condition_label="selected_path == skip_bearish_buy",
                ),
                GraphEdge(
                    "risk_manager",
                    "finalize",
                    output_schema=risk_output,
                    input_schema=risk_input,
                ),
                GraphEdge(
                    "market_bearish_skip",
                    "finalize",
                    output_schema=skip_output,
                    input_schema=skip_input,
                ),
            ),
        )

    def _run_c1_node(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        query = C1DecisionQuery.from_mapping(state["c1_query"])
        return {"c1_decision": self._c1_runtime.run(query).to_mapping()}

    def _run_trader_node(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._trader_runtime.run_graph_node(_plain_state(state))

    def _run_risk_node(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        risk_state = dict(_plain_state(state))
        risk_state["risk_context"] = risk_state["effective_risk_context"]
        return self._risk_manager_runtime.run_graph_node(risk_state)

    def run(
        self,
        query: FinancialGraphQuery | None = None,
        *,
        resume: bool = False,
    ) -> FinancialGraphResult:
        if resume:
            if query is not None:
                raise FinancialGraphError("resume must not include a new query")
            graph_result = self._runner.run(resume=True)
        else:
            if not isinstance(query, FinancialGraphQuery):
                raise FinancialGraphError("query must be a FinancialGraphQuery")
            graph_result = self._runner.run(query.to_mapping())
        report = graph_result.state["financial_report"]
        validation = validate_financial_graph_report(report)
        if not validation.valid:
            raise FinancialGraphError(validation.detail)
        return FinancialGraphResult(
            report=report,
            graph=self._graph_metadata(graph_result),
        )

    @staticmethod
    def _graph_metadata(result: GraphResult) -> dict[str, Any]:
        return {
            "statuses": dict(result.statuses),
            "execution_order": list(result.execution_order),
            "attempts": dict(result.attempts),
            "trace": [
                {
                    "event": event.event,
                    "node": event.node,
                    "attempt": event.attempt,
                    "detail": event.detail,
                }
                for event in result.trace
            ],
        }

    def run_graph_node(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        query = FinancialGraphQuery.from_mapping(state)
        return {"financial_graph": self.run(query).to_mapping()}


def build_default_financial_graph_runtime(
    *,
    project_root: str | Path | None = None,
    policy: FinancialDataPolicy | None = None,
    checkpoint_path: str | Path | None = None,
) -> FinancialGraphRuntime:
    return FinancialGraphRuntime(
        c1_runtime=build_default_c1_decision_runtime(
            project_root=project_root,
            policy=policy,
        ),
        trader_runtime=build_default_trader_runtime(),
        risk_manager_runtime=build_default_risk_manager_runtime(),
        checkpoint_store=(
            JsonCheckpointStore(Path(checkpoint_path))
            if checkpoint_path is not None
            else None
        ),
    )


__all__ = [
    "FinancialGraphError",
    "FinancialGraphQuery",
    "FinancialGraphResult",
    "FinancialGraphRuntime",
    "build_default_financial_graph_runtime",
    "validate_financial_graph_report",
]
