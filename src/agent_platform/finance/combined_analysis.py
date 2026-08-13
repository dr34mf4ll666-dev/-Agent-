"""C1 first slice: parallel orchestration of the four specialist Agents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_platform.core import (
    CrossValidationResult,
    GraphDefinition,
    GraphEdge,
    GraphExecutionPolicy,
    GraphResult,
    GraphRunner,
    GraphState,
    JsonCheckpointStore,
)

from .data_hub import FinancialDataPolicy
from .fundamental_runtime import (
    FundamentalAnalysisQuery,
    FundamentalAnalysisRuntime,
    build_default_fundamental_analysis_runtime,
)
from .industry_runtime import (
    IndustryAnalysisQuery,
    IndustryAnalysisRuntime,
    build_default_industry_analysis_runtime,
)
from .macro_runtime import (
    MacroAnalysisQuery,
    MacroAnalysisRuntime,
    build_default_macro_analysis_runtime,
)
from .technical_runtime import (
    TechnicalAnalysisQuery,
    TechnicalAnalysisRuntime,
    build_default_technical_analysis_runtime,
)


SPECIALIST_NAMES = ("technical", "fundamental", "industry", "macro")
QUERY_KEYS = {name: f"{name}_query" for name in SPECIALIST_NAMES}
REPORT_KEYS = {name: f"{name}_report" for name in SPECIALIST_NAMES}
EVIDENCE_KEYS = {name: f"{name}_evidence" for name in SPECIALIST_NAMES}
LOOP_KEYS = {name: f"{name}_loop" for name in SPECIALIST_NAMES}


class CombinedAnalysisError(ValueError):
    """The C1 combined-analysis request or bundle is invalid."""


@dataclass(frozen=True)
class CombinedAnalysisQuery:
    """Small external interface hiding four specialist query contracts."""

    technical: TechnicalAnalysisQuery
    fundamental: FundamentalAnalysisQuery
    industry: IndustryAnalysisQuery
    macro: MacroAnalysisQuery

    def __post_init__(self) -> None:
        if not all(
            isinstance(query, expected)
            for query, expected in (
                (self.technical, TechnicalAnalysisQuery),
                (self.fundamental, FundamentalAnalysisQuery),
                (self.industry, IndustryAnalysisQuery),
                (self.macro, MacroAnalysisQuery),
            )
        ):
            raise CombinedAnalysisError("all specialist queries are required")
        symbols = {
            self.technical.symbol,
            self.fundamental.symbol,
            self.macro.symbol,
        }
        if len(symbols) != 1:
            raise CombinedAnalysisError(
                "technical, fundamental, and macro queries must use the same symbol"
            )
        modes = {
            self.technical.mode,
            self.fundamental.mode,
            self.industry.mode,
            self.macro.mode,
        }
        if len(modes) != 1:
            raise CombinedAnalysisError(
                "all specialist queries must use the same mode"
            )

    @property
    def symbol(self) -> str:
        return self.technical.symbol

    @property
    def mode(self) -> str:
        return self.technical.mode

    def to_mapping(self) -> dict[str, Any]:
        return {
            "technical_query": self.technical.to_mapping(),
            "fundamental_query": self.fundamental.to_mapping(),
            "industry_query": self.industry.to_mapping(),
            "macro_query": self.macro.to_mapping(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CombinedAnalysisQuery":
        if not isinstance(value, Mapping):
            raise CombinedAnalysisError("combined query must be an object")
        try:
            return cls(
                technical=TechnicalAnalysisQuery.from_mapping(value["technical_query"]),
                fundamental=FundamentalAnalysisQuery.from_mapping(
                    value["fundamental_query"]
                ),
                industry=IndustryAnalysisQuery.from_mapping(value["industry_query"]),
                macro=MacroAnalysisQuery.from_mapping(value["macro_query"]),
            )
        except KeyError as error:
            raise CombinedAnalysisError(
                f"combined query is missing {error.args[0]}"
            ) from error

    @classmethod
    def for_symbol(
        cls,
        symbol: str = "sz000001",
        *,
        sector: str = "玻璃行业",
        index_symbol: str = "sh000300",
        mode: str = "offline",
        start_date: str = "20240101",
        end_date: str = "20260807",
    ) -> "CombinedAnalysisQuery":
        return cls(
            technical=TechnicalAnalysisQuery(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                mode=mode,
                limit=30,
            ),
            fundamental=FundamentalAnalysisQuery(
                symbol=symbol,
                mode=mode,
                limit=4,
                start_year=start_date[:4],
            ),
            industry=IndustryAnalysisQuery(
                sector=sector,
                mode=mode,
                limit=5,
                start_date=start_date,
                end_date=end_date,
            ),
            macro=MacroAnalysisQuery(
                index_symbol=index_symbol,
                symbol=symbol,
                mode=mode,
                limit=5,
                start_date=start_date,
                end_date=end_date,
            ),
        )


def _object_schema(required: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(required),
        "properties": {key: {"type": "object"} for key in required},
        "additionalProperties": True,
    }


def _presence_schema(required: tuple[str, ...]) -> dict[str, Any]:
    """Require named fields while preserving specialist-specific value types."""

    return {
        "type": "object",
        "required": list(required),
        "additionalProperties": True,
    }


PLANNER_OUTPUT_SCHEMA = _object_schema(
    ("planner", "technical_query", "fundamental_query", "industry_query", "macro_query")
)


def _specialist_output_schema(name: str) -> dict[str, Any]:
    return _presence_schema(
        (REPORT_KEYS[name], EVIDENCE_KEYS[name], LOOP_KEYS[name])
    )


def _planner_node(state: GraphState) -> Mapping[str, Any]:
    query = CombinedAnalysisQuery.from_mapping(state["combined_query"])
    return {
        "planner": {
            "status": "planned",
            "goal": "run four specialist analyses with independent evidence",
            "parallel_agents": list(SPECIALIST_NAMES),
            "mode": query.mode,
            "symbol": query.symbol,
            "next_stage": "C1 debate and synthesis pending",
        },
        **query.to_mapping(),
    }


def _runtime_node(
    runtime: Any,
) -> Callable[[GraphState], Mapping[str, Any]]:
    def run(state: GraphState) -> Mapping[str, Any]:
        return runtime.run_graph_node(state)

    return run


def validate_combined_analysis_bundle(value: Any) -> CrossValidationResult:
    """Check the aggregate shape without inventing a cross-agent conclusion."""

    if not isinstance(value, Mapping):
        return CrossValidationResult(False, "combined analysis must be an object")
    if value.get("status") != "specialists_completed":
        return CrossValidationResult(False, "combined analysis has an invalid status")
    reports = value.get("reports")
    evidence = value.get("evidence")
    loops = value.get("loops")
    summary = value.get("summary")
    if not all(isinstance(item, Mapping) for item in (reports, evidence, loops, summary)):
        return CrossValidationResult(False, "combined analysis sections must be objects")
    for name in SPECIALIST_NAMES:
        if name not in reports or name not in evidence or name not in loops:
            return CrossValidationResult(
                False, f"combined analysis is missing {name} specialist evidence"
            )
        if not isinstance(reports[name], Mapping):
            return CrossValidationResult(False, f"{name} report must be an object")
        if not isinstance(evidence[name], (Mapping, list)):
            return CrossValidationResult(
                False, f"{name} evidence must be an object or array"
            )
        if not isinstance(loops[name], Mapping):
            return CrossValidationResult(False, f"{name} loop must be an object")
    if set(summary) != set(SPECIALIST_NAMES):
        return CrossValidationResult(False, "combined summary must cover all specialists")
    return CrossValidationResult(True)


def _aggregate_node(state: GraphState) -> Mapping[str, Any]:
    query = CombinedAnalysisQuery.from_mapping(state["combined_query"])
    reports = {name: state[REPORT_KEYS[name]] for name in SPECIALIST_NAMES}
    evidence = {name: state[EVIDENCE_KEYS[name]] for name in SPECIALIST_NAMES}
    loops = {name: state[LOOP_KEYS[name]] for name in SPECIALIST_NAMES}
    summary = {
        "technical": {
            "signal_label": reports["technical"]["signal_label"],
            "signal_score": reports["technical"]["signal_score"],
        },
        "fundamental": {
            "score_label": reports["fundamental"]["score_label"],
            "score": reports["fundamental"]["score"],
        },
        "industry": {
            "score_label": reports["industry"]["score_label"],
            "score": reports["industry"]["score"],
            "prosperity": reports["industry"]["prosperity"]["label"],
        },
        "macro": {
            "score_label": reports["macro"]["score_label"],
            "score": reports["macro"]["score"],
            "market_regime": reports["macro"]["market_regime"]["label"],
            "risk_appetite": reports["macro"]["risk_appetite"]["label"],
        },
    }
    sources = sorted(
        {
            source
            for name in SPECIALIST_NAMES
            for source in reports[name].get("sources", [])
        }
    )
    combined = {
        "status": "specialists_completed",
        "symbol": query.symbol,
        "mode": query.mode,
        "reports": reports,
        "evidence": evidence,
        "loops": loops,
        "summary": summary,
        "sources": sources,
        "next_stage": "bull_bear_debate_and_synthesis",
        "caveats": [
            "this C1 slice aggregates four specialist reports but does not produce a final investment conclusion",
            "Bull/Bear debate, target-price interval, confidence, and bias checks are not implemented in this slice",
        ],
    }
    validation = validate_combined_analysis_bundle(combined)
    if not validation.valid:
        raise CombinedAnalysisError(validation.detail)
    return {"combined_analysis": combined}


@dataclass(frozen=True)
class CombinedAnalysisWorkflowResult:
    """Combined reports plus Graph scheduling evidence."""

    report: Mapping[str, Any]
    graph_result: GraphResult

    def to_mapping(self) -> dict[str, Any]:
        return {
            "report": dict(self.report),
            "graph": {
                "statuses": dict(self.graph_result.statuses),
                "execution_order": list(self.graph_result.execution_order),
                "attempts": dict(self.graph_result.attempts),
                "trace": [
                    {
                        "event": event.event,
                        "node": event.node,
                        "attempt": event.attempt,
                        "detail": event.detail,
                    }
                    for event in self.graph_result.trace
                ],
            },
        }


class CombinedAnalysisRuntime:
    """Deep C1 seam: one call to run the four specialist Agents together."""

    def __init__(
        self,
        *,
        technical: TechnicalAnalysisRuntime,
        fundamental: FundamentalAnalysisRuntime,
        industry: IndustryAnalysisRuntime,
        macro: MacroAnalysisRuntime,
        checkpoint_store: JsonCheckpointStore | None = None,
        event_sink: Callable[[Any], None] | None = None,
    ) -> None:
        self._runtimes = {
            "technical": technical,
            "fundamental": fundamental,
            "industry": industry,
            "macro": macro,
        }
        self._checkpoint_store = checkpoint_store
        self._event_sink = event_sink

    def _build_graph(self) -> GraphDefinition:
        nodes: dict[str, Callable[[GraphState], Mapping[str, Any]]] = {
            "planner": _planner_node,
            "technical": _runtime_node(self._runtimes["technical"]),
            "fundamental": _runtime_node(self._runtimes["fundamental"]),
            "industry": _runtime_node(self._runtimes["industry"]),
            "macro": _runtime_node(self._runtimes["macro"]),
            "aggregate": _aggregate_node,
        }
        edges = [
            GraphEdge(
                "planner",
                name,
                output_schema=PLANNER_OUTPUT_SCHEMA,
                input_schema=_object_schema((QUERY_KEYS[name],)),
            )
            for name in SPECIALIST_NAMES
        ]
        edges.extend(
            GraphEdge(
                name,
                "aggregate",
                output_schema=_specialist_output_schema(name),
                input_schema=_presence_schema(
                    (REPORT_KEYS[name], EVIDENCE_KEYS[name], LOOP_KEYS[name])
                ),
            )
            for name in SPECIALIST_NAMES
        )
        return GraphDefinition(
            start="planner",
            nodes=nodes,
            edges=tuple(edges),
            execution=GraphExecutionPolicy(strategy="parallel", max_workers=4),
        )

    def run(
        self,
        query: CombinedAnalysisQuery | None = None,
        *,
        resume: bool = False,
    ) -> CombinedAnalysisWorkflowResult:
        if resume:
            if query is not None:
                raise CombinedAnalysisError("resume must not include a new query")
        elif not isinstance(query, CombinedAnalysisQuery):
            raise CombinedAnalysisError("query must be a CombinedAnalysisQuery")
        runner = GraphRunner(
            self._build_graph(),
            checkpoint_store=self._checkpoint_store,
            event_sink=self._event_sink,
        )
        graph_result = (
            runner.run(resume=True)
            if resume
            else runner.run({"combined_query": query.to_mapping()})
        )
        report = graph_result.state.get("combined_analysis")
        if not isinstance(report, Mapping):
            raise CombinedAnalysisError("combined graph produced no aggregate report")
        return CombinedAnalysisWorkflowResult(report, graph_result)

    def run_graph_node(self, state: GraphState) -> Mapping[str, Any]:
        query = CombinedAnalysisQuery.from_mapping(state["combined_query"])
        return {"combined_analysis": self.run(query).to_mapping()}


def build_default_combined_analysis_runtime(
    *,
    project_root: str | Path | None = None,
    policy: FinancialDataPolicy | None = None,
    checkpoint_path: str | Path | None = None,
    event_sink: Callable[[Any], None] | None = None,
) -> CombinedAnalysisRuntime:
    return CombinedAnalysisRuntime(
        technical=build_default_technical_analysis_runtime(
            project_root=project_root, policy=policy
        ),
        fundamental=build_default_fundamental_analysis_runtime(
            project_root=project_root, policy=policy
        ),
        industry=build_default_industry_analysis_runtime(
            project_root=project_root, policy=policy
        ),
        macro=build_default_macro_analysis_runtime(
            project_root=project_root, policy=policy
        ),
        checkpoint_store=(
            JsonCheckpointStore(Path(checkpoint_path))
            if checkpoint_path is not None
            else None
        ),
        event_sink=event_sink,
    )


__all__ = [
    "CombinedAnalysisError",
    "CombinedAnalysisQuery",
    "CombinedAnalysisRuntime",
    "CombinedAnalysisWorkflowResult",
    "PLANNER_OUTPUT_SCHEMA",
    "SPECIALIST_NAMES",
    "build_default_combined_analysis_runtime",
    "validate_combined_analysis_bundle",
]
