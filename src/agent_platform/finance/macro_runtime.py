"""B2 market and macro specialist runtime over Data Hub, Loop, Harness, and Graph."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_platform.core import (
    Action,
    AgentRequest,
    CognitiveLoopResult,
    CognitiveLoopRunner,
    CognitiveLoopState,
    CrossValidationResult,
    CrossValidator,
    GraphState,
    JSONSchemaValidator,
    Observation,
    Plan,
    Reflection,
    ReflectionDecision,
    SourceAttributionFilter,
    ToolRegistry,
    build_default_agent_tool_policy_registry,
)

from .data_hub import (
    FinancialDataError,
    FinancialDataErrorCode,
    FinancialDataHub,
    FinancialDataPolicy,
    FinancialDataTool,
    FixtureFinancialDataProvider,
    JsonFinancialDataCache,
    SubprocessFinancialDataProvider,
)
from .macro import MacroAnalysisEngine, MacroAnalysisError


MACRO_DATASET_KEYS = {
    "macro_index": "macro.index",
    "fund_flow": "market.fund_flow",
    "macro_gdp": "macro.gdp",
    "macro_shibor": "macro.shibor",
    "policy_lpr": "macro.policy_lpr",
    "research": "sentiment.research",
}

DERIVED_EMPTY_RESEARCH_SOURCE = "derived:akshare.stock_research_report_em:empty"
DERIVED_EMPTY_FUND_FLOW_SOURCE = "derived:akshare.stock_fund_flow_individual:unavailable"


def _empty_fund_flow_evidence(
    macro_data: Mapping[str, Any],
    *,
    symbol: str,
    mode: str,
    reason: str,
) -> dict[str, Any]:
    index_data = macro_data.get("macro_index")
    if not isinstance(index_data, Mapping) or not index_data.get("timestamp"):
        raise MacroAnalysisError("cannot timestamp unavailable fund-flow evidence")
    timestamp = str(index_data["timestamp"])
    return {
        "dataset": "market.fund_flow",
        "record_count": 1,
        "source": DERIVED_EMPTY_FUND_FLOW_SOURCE,
        "timestamp": timestamp,
        "attempts": 0,
        "cache_hit": False,
        "mode": mode,
        "records": [
            {
                "subject": symbol,
                "fields": {
                    "availability": "not_available",
                    "reason": reason,
                },
                "source": DERIVED_EMPTY_FUND_FLOW_SOURCE,
                "timestamp": timestamp,
                "as_of": timestamp,
            }
        ],
        "trace": [
            {
                "event": "provider.fallback.unavailable_fund_flow",
                "attempt": 0,
                "detail": reason,
            }
        ],
    }


def _empty_research_evidence(
    macro_data: Mapping[str, Any],
    *,
    symbol: str,
    mode: str,
) -> dict[str, Any]:
    timestamps = [
        str(dataset["timestamp"])
        for dataset in macro_data.values()
        if isinstance(dataset, Mapping) and dataset.get("timestamp")
    ]
    if not timestamps:
        raise MacroAnalysisError("cannot timestamp empty research evidence")
    timestamp = max(timestamps)
    return {
        "dataset": "sentiment.research",
        "record_count": 1,
        "source": DERIVED_EMPTY_RESEARCH_SOURCE,
        "timestamp": timestamp,
        "attempts": 0,
        "cache_hit": False,
        "mode": mode,
        "records": [
            {
                "subject": symbol,
                "fields": {
                    "rating": "not_available",
                    "derivation": "provider returned no research reports; neutral evidence used",
                },
                "source": DERIVED_EMPTY_RESEARCH_SOURCE,
                "timestamp": timestamp,
                "as_of": timestamp,
            }
        ],
        "trace": [
            {
                "event": "provider.fallback.empty_research",
                "attempt": 0,
                "detail": "no research reports; deterministic neutral evidence used",
            }
        ],
    }


MACRO_TOOL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["query", "analysis", "macro_data"],
    "properties": {
        "query": {
            "type": "object",
            "required": [
                "index_symbol",
                "symbol",
                "mode",
                "limit",
                "start_date",
                "end_date",
            ],
            "properties": {
                "index_symbol": {"type": "string", "minLength": 8},
                "symbol": {"type": "string", "minLength": 8},
                "mode": {"enum": ["offline", "live"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                "start_date": {"type": "string", "minLength": 8, "maxLength": 8},
                "end_date": {"type": "string", "minLength": 8, "maxLength": 8},
            },
            "additionalProperties": False,
        },
        "analysis": {
            "type": "object",
            "required": [
                "index_symbol",
                "symbol",
                "as_of",
                "timestamp",
                "index",
                "funds",
                "sentiment",
                "macro",
                "market_regime",
                "risk_appetite",
                "score",
                "score_label",
                "score_components",
                "sources",
                "caveats",
            ],
            "properties": {
                "index_symbol": {"type": "string", "minLength": 1},
                "symbol": {"type": "string", "minLength": 1},
                "as_of": {"type": "string", "minLength": 1},
                "timestamp": {"type": "string", "minLength": 1},
                "index": {"type": "object"},
                "funds": {"type": "object"},
                "sentiment": {"type": "object"},
                "macro": {"type": "object"},
                "market_regime": {"type": "object"},
                "risk_appetite": {"type": "object"},
                "score": {"type": "integer", "minimum": -100, "maximum": 100},
                "score_label": {
                    "enum": [
                        "strong_positive",
                        "positive",
                        "neutral",
                        "negative",
                        "strong_negative",
                    ]
                },
                "score_components": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "required": ["name", "points", "rule"],
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "points": {"type": "integer", "minimum": -20, "maximum": 20},
                            "rule": {"type": "string", "minLength": 1},
                        },
                        "additionalProperties": False,
                    },
                },
                "sources": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
                "caveats": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
            },
            "additionalProperties": False,
        },
        "macro_data": {
            "type": "object",
            "required": list(MACRO_DATASET_KEYS),
            "properties": {key: {"type": "object"} for key in MACRO_DATASET_KEYS},
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class MacroAnalysisQuery:
    """Stable request understood by standalone and Graph callers."""

    index_symbol: str = "sh000300"
    symbol: str = "sz000001"
    mode: str = "offline"
    limit: int = 5
    start_date: str = "20240101"
    end_date: str = "20260807"

    def __post_init__(self) -> None:
        for name, value in (("index_symbol", self.index_symbol), ("symbol", self.symbol)):
            if not isinstance(value, str) or not re.fullmatch(
                r"(?:sh|sz|bj)\d{6}", value.lower()
            ):
                raise MacroAnalysisError(
                    f"{name} must include a market prefix, for example sz000001"
                )
            object.__setattr__(self, name, value.lower())
        if self.mode not in {"offline", "live"}:
            raise MacroAnalysisError("mode must be offline or live")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 30
        ):
            raise MacroAnalysisError("limit must be an integer from 1 to 30")
        if not re.fullmatch(r"\d{8}", self.start_date) or not re.fullmatch(
            r"\d{8}", self.end_date
        ):
            raise MacroAnalysisError("start_date and end_date must use YYYYMMDD")
        if int(self.start_date) > int(self.end_date):
            raise MacroAnalysisError("start_date must not be later than end_date")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "index_symbol": self.index_symbol,
            "symbol": self.symbol,
            "mode": self.mode,
            "limit": self.limit,
            "start_date": self.start_date,
            "end_date": self.end_date,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MacroAnalysisQuery":
        if not isinstance(value, Mapping):
            raise MacroAnalysisError("macro query must be an object")
        return cls(
            index_symbol=value.get("index_symbol", "sh000300"),
            symbol=value.get("symbol", "sz000001"),
            mode=value.get("mode", "offline"),
            limit=value.get("limit", 5),
            start_date=value.get("start_date", "20240101"),
            end_date=value.get("end_date", "20260807"),
        )


def _bundle_for_engine(macro_data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(macro_data, Mapping):
        raise MacroAnalysisError("macro_data must be an object")
    bundle: dict[str, Any] = {}
    for key in MACRO_DATASET_KEYS:
        if key not in macro_data:
            raise MacroAnalysisError(f"macro_data is missing {key}")
        bundle[key] = macro_data[key]
    return bundle


class _MacroAnalysisTool:
    name = "macro_analysis"

    def __init__(
        self,
        financial_tool: FinancialDataTool,
        engine: MacroAnalysisEngine,
    ) -> None:
        self._financial_tool = financial_tool
        self._engine = engine

    def run(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = MacroAnalysisQuery.from_mapping(arguments)
        macro_data: dict[str, Any] = {}
        macro_data["macro_index"] = self._financial_tool.run(
            {
                "dataset": "macro.index",
                "params": {"symbol": query.index_symbol, "limit": query.limit},
                "mode": query.mode,
            }
        )
        try:
            macro_data["fund_flow"] = self._financial_tool.run(
                {
                    "dataset": "market.fund_flow",
                    "params": {"symbol": query.symbol, "limit": 1},
                    "mode": query.mode,
                }
            )
        except FinancialDataError as error:
            can_fallback = (
                query.mode == "live"
                and error.code
                in {
                    FinancialDataErrorCode.EMPTY_RESPONSE,
                    FinancialDataErrorCode.TIMEOUT,
                    FinancialDataErrorCode.PROVIDER_UNAVAILABLE,
                    FinancialDataErrorCode.RATE_LIMITED,
                }
            )
            if not can_fallback:
                raise
            macro_data["fund_flow"] = _empty_fund_flow_evidence(
                macro_data,
                symbol=query.symbol,
                mode=query.mode,
                reason="fund-flow provider was temporarily unavailable; neutral evidence used",
            )
        macro_data["macro_gdp"] = self._financial_tool.run(
            {
                "dataset": "macro.gdp",
                "params": {"limit": query.limit},
                "mode": query.mode,
            }
        )
        macro_data["macro_shibor"] = self._financial_tool.run(
            {
                "dataset": "macro.shibor",
                "params": {"limit": query.limit},
                "mode": query.mode,
            }
        )
        macro_data["policy_lpr"] = self._financial_tool.run(
            {
                "dataset": "macro.policy_lpr",
                "params": {
                    "limit": query.limit,
                    "start_date": query.start_date,
                    "end_date": query.end_date,
                },
                "mode": query.mode,
            }
        )
        try:
            macro_data["research"] = self._financial_tool.run(
                {
                    "dataset": "sentiment.research",
                    "params": {
                        "symbol": query.symbol,
                        "limit": query.limit,
                        "start_date": query.start_date,
                        "end_date": query.end_date,
                    },
                    "mode": query.mode,
                }
            )
        except FinancialDataError as error:
            if not (
                query.mode == "live"
                and error.code
                in {
                    FinancialDataErrorCode.EMPTY_RESPONSE,
                    FinancialDataErrorCode.TIMEOUT,
                    FinancialDataErrorCode.PROVIDER_UNAVAILABLE,
                    FinancialDataErrorCode.RATE_LIMITED,
                }
            ):
                raise
            macro_data["research"] = _empty_research_evidence(
                macro_data,
                symbol=query.symbol,
                mode=query.mode,
            )
        analysis = self._engine.analyze(
            _bundle_for_engine(macro_data),
            index_symbol=query.index_symbol,
            symbol=query.symbol,
        )
        return {
            "query": query.to_mapping(),
            "analysis": analysis,
            "macro_data": macro_data,
        }


def validate_macro_analysis_output(value: Any) -> CrossValidationResult:
    """Recompute macro metrics and reject altered conclusions."""

    if not isinstance(value, Mapping):
        return CrossValidationResult(False, "macro output must be an object")
    query = value.get("query")
    analysis = value.get("analysis")
    macro_data = value.get("macro_data")
    try:
        parsed_query = MacroAnalysisQuery.from_mapping(query)
        expected = MacroAnalysisEngine().analyze(
            _bundle_for_engine(macro_data),
            index_symbol=parsed_query.index_symbol,
            symbol=parsed_query.symbol,
        )
    except Exception as error:
        return CrossValidationResult(False, f"cannot recompute macro analysis: {error}")
    if dict(analysis) != expected:
        return CrossValidationResult(False, "macro analysis does not match recomputed values")
    return CrossValidationResult(True)


class _MacroLoopAgent:
    name = "macro_analysis_loop"

    def create_plan(self, request: AgentRequest) -> Plan:
        MacroAnalysisQuery.from_mapping(request.context.get("macro_query", {}))
        return Plan(
            goal="produce a source-backed deterministic market and macro report",
            steps=(
                "fetch index, funds, macro, policy, and sentiment data",
                "calculate trend, regime, and risk appetite",
                "verify report",
            ),
        )

    def choose_action(self, state: CognitiveLoopState) -> Action:
        return Action(
            tool="macro_analysis",
            arguments=state.request.context["macro_query"],
            rationale="fetch controlled market and macro datasets and calculate deterministic analysis",
        )

    def reflect(
        self,
        state: CognitiveLoopState,
        observation: Observation,
    ) -> Reflection:
        del state
        if not observation.success:
            return Reflection(
                decision=ReflectionDecision.REVISE,
                reason="the controlled market or macro data tool failed; retry within the hard limit",
            )
        output = observation.output
        if not isinstance(output, Mapping) or not isinstance(output.get("analysis"), Mapping):
            return Reflection(
                decision=ReflectionDecision.REVISE,
                reason="the macro observation shape is incomplete",
            )
        analysis = output["analysis"]
        return Reflection(
            decision=ReflectionDecision.COMPLETE,
            reason="index, funds, sentiment, regime, provenance, schema, and deterministic cross-check passed",
            final_answer=(
                f"{analysis['index_symbol']} 大盘/宏观分析完成：Market Regime "
                f"{analysis['market_regime']['label']}，风险偏好 {analysis['risk_appetite']['label']}。"
                "结果不构成投资建议。"
            ),
        )


@dataclass(frozen=True)
class MacroAnalysisWorkflowResult:
    """Convenient result while preserving complete Loop evidence."""

    report: Mapping[str, Any]
    loop_result: CognitiveLoopResult

    def to_mapping(self) -> dict[str, Any]:
        return {
            "report": dict(self.report),
            "loop": {
                "steps": self.loop_result.state.step_count,
                "allowed_tools": [
                    record.action.tool for record in self.loop_result.tool_records
                ],
                "trace": [
                    {
                        "event": event.event,
                        "step": event.step,
                        "attempt": event.attempt,
                        "detail": event.detail,
                    }
                    for event in self.loop_result.trace
                ],
                "harness_trace": [
                    {
                        "event": event.event,
                        "agent": event.agent,
                        "detail": event.detail,
                    }
                    for record in self.loop_result.tool_records
                    for attempt_trace in record.harness_traces
                    for event in attempt_trace
                ],
            },
        }


class MacroAnalysisRuntime:
    """One deep interface for standalone and Graph market/macro analysis."""

    def __init__(
        self,
        financial_tool: FinancialDataTool,
        *,
        engine: MacroAnalysisEngine | None = None,
    ) -> None:
        self._engine = engine or MacroAnalysisEngine()
        self._tool = _MacroAnalysisTool(financial_tool, self._engine)

    def run(self, query: MacroAnalysisQuery) -> MacroAnalysisWorkflowResult:
        if not isinstance(query, MacroAnalysisQuery):
            raise MacroAnalysisError("query must be a MacroAnalysisQuery")
        source_paths = tuple(
            f"metadata.observation.output.macro_data.{key}.records"
            for key in MACRO_DATASET_KEYS
        )
        runner = CognitiveLoopRunner(
            agent=_MacroLoopAgent(),
            tools=ToolRegistry(
                [self._tool],
                agent_name=_MacroLoopAgent.name,
                permission_registry=build_default_agent_tool_policy_registry(),
            ),
            tool_guardrails=(
                JSONSchemaValidator(
                    output_schema=MACRO_TOOL_OUTPUT_SCHEMA,
                    output_path="metadata.observation.output",
                    name="macro_output_schema",
                ),
                SourceAttributionFilter(
                    required_fields=("source", "timestamp", "as_of"),
                    output_paths=source_paths,
                    name="macro_market_sources",
                ),
                CrossValidator(
                    validate_macro_analysis_output,
                    output_path="metadata.observation.output",
                    name="macro_value_recompute",
                ),
            ),
            max_steps=2,
            max_tool_retries=0,
            memory_capacity=12,
        )
        loop_result = runner.run(
            AgentRequest(
                task=f"analyze market and macro for {query.index_symbol}",
                context={"macro_query": query.to_mapping()},
            )
        )
        successful = [
            record.observation.output
            for record in loop_result.tool_records
            if record.observation.success
        ]
        if not successful or not isinstance(successful[-1], Mapping):
            raise MacroAnalysisError("macro loop produced no valid report")
        return MacroAnalysisWorkflowResult(successful[-1], loop_result)

    def run_graph_node(self, state: GraphState) -> Mapping[str, Any]:
        query = MacroAnalysisQuery.from_mapping(state["macro_query"])
        result = self.run(query).to_mapping()
        return {
            "macro_report": result["report"]["analysis"],
            "macro_evidence": result["report"]["macro_data"],
            "macro_loop": result["loop"],
        }


def build_default_macro_analysis_runtime(
    *,
    project_root: str | Path | None = None,
    policy: FinancialDataPolicy | None = None,
) -> MacroAnalysisRuntime:
    root = Path(project_root) if project_root is not None else Path.cwd()
    hub = FinancialDataHub(
        live_provider=SubprocessFinancialDataProvider(),
        offline_provider=FixtureFinancialDataProvider(
            root / "tests" / "fixtures" / "macro_analysis.json"
        ),
        cache=JsonFinancialDataCache(root / ".runtime" / "finance" / "data_cache.json"),
        policy=policy,
    )
    return MacroAnalysisRuntime(FinancialDataTool(hub))


__all__ = [
    "DERIVED_EMPTY_FUND_FLOW_SOURCE",
    "DERIVED_EMPTY_RESEARCH_SOURCE",
    "MACRO_DATASET_KEYS",
    "MACRO_TOOL_OUTPUT_SCHEMA",
    "MacroAnalysisQuery",
    "MacroAnalysisRuntime",
    "MacroAnalysisWorkflowResult",
    "build_default_macro_analysis_runtime",
    "validate_macro_analysis_output",
]
