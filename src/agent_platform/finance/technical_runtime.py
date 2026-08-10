"""B2 technical specialist runtime over Data Hub, Cognitive Loop, and Harness."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
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

from .contracts import MarketDataSeries
from .data_hub import (
    FinancialDataHub,
    FinancialDataPolicy,
    FinancialDataTool,
    FixtureFinancialDataProvider,
    JsonFinancialDataCache,
    SubprocessFinancialDataProvider,
)
from .technical import TechnicalAnalysisEngine, TechnicalAnalysisError


TECHNICAL_TOOL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["query", "analysis", "market_data"],
    "properties": {
        "query": {
            "type": "object",
            "required": ["symbol", "start_date", "end_date", "mode", "limit"],
            "properties": {
                "symbol": {"type": "string", "minLength": 8},
                "start_date": {"type": "string", "minLength": 8},
                "end_date": {"type": "string", "minLength": 8},
                "mode": {"enum": ["offline", "live"]},
                "limit": {"type": "integer", "minimum": 30, "maximum": 500},
            },
            "additionalProperties": False,
        },
        "analysis": {
            "type": "object",
            "required": [
                "symbol",
                "as_of",
                "timestamp",
                "sample_size",
                "latest_close",
                "daily_return",
                "ma",
                "macd",
                "rsi",
                "kdj",
                "bollinger",
                "levels",
                "trend",
                "trend_rule",
                "signal_score",
                "signal_label",
                "score_components",
                "sources",
            ],
            "properties": {
                "symbol": {"type": "string", "minLength": 1},
                "as_of": {"type": "string", "minLength": 1},
                "timestamp": {"type": "string", "minLength": 1},
                "sample_size": {"type": "integer", "minimum": 30},
                "latest_close": {"type": "string", "minLength": 1},
                "daily_return": {"type": "string", "minLength": 1},
                "ma": {"type": "object"},
                "macd": {"type": "object"},
                "rsi": {"type": "object"},
                "kdj": {"type": "object"},
                "bollinger": {"type": "object"},
                "levels": {"type": "object"},
                "trend": {"enum": ["bullish", "bearish", "mixed"]},
                "trend_rule": {"type": "string", "minLength": 1},
                "signal_score": {"type": "integer", "minimum": -100, "maximum": 100},
                "signal_label": {
                    "enum": [
                        "strong_bullish",
                        "bullish",
                        "neutral",
                        "bearish",
                        "strong_bearish",
                    ]
                },
                "score_components": {
                    "type": "array",
                    "minItems": 7,
                    "maxItems": 7,
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
            },
            "additionalProperties": False,
        },
        "market_data": {
            "type": "array",
            "minItems": 30,
            "items": {
                "type": "object",
                "required": [
                    "symbol",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "source",
                    "timestamp",
                    "as_of",
                ],
                "properties": {
                    "symbol": {"type": "string", "minLength": 1},
                    "open": {"type": "string", "minLength": 1},
                    "high": {"type": "string", "minLength": 1},
                    "low": {"type": "string", "minLength": 1},
                    "close": {"type": "string", "minLength": 1},
                    "volume": {"type": "integer", "minimum": 0},
                    "source": {"type": "string", "minLength": 1},
                    "timestamp": {"type": "string", "minLength": 1},
                    "as_of": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class TechnicalAnalysisQuery:
    """Stable request understood by standalone and Graph callers."""

    symbol: str
    start_date: str
    end_date: str
    mode: str = "offline"
    limit: int = 60

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not re.fullmatch(
            r"(?:sh|sz|bj)\d{6}", self.symbol.lower()
        ):
            raise TechnicalAnalysisError(
                "symbol must include a market prefix, for example sz000001"
            )
        object.__setattr__(self, "symbol", self.symbol.lower())
        try:
            start = datetime.strptime(self.start_date, "%Y%m%d")
            end = datetime.strptime(self.end_date, "%Y%m%d")
        except (TypeError, ValueError) as error:
            raise TechnicalAnalysisError(
                "start_date and end_date must use YYYYMMDD"
            ) from error
        if start > end:
            raise TechnicalAnalysisError("start_date must not be later than end_date")
        if self.mode not in {"offline", "live"}:
            raise TechnicalAnalysisError("mode must be offline or live")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 30 <= self.limit <= 500
        ):
            raise TechnicalAnalysisError("limit must be an integer from 30 to 500")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "mode": self.mode,
            "limit": self.limit,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TechnicalAnalysisQuery":
        if not isinstance(value, Mapping):
            raise TechnicalAnalysisError("technical query must be an object")
        try:
            return cls(
                symbol=value["symbol"],
                start_date=value["start_date"],
                end_date=value["end_date"],
                mode=value.get("mode", "offline"),
                limit=value.get("limit", 60),
            )
        except KeyError as error:
            raise TechnicalAnalysisError(
                f"technical query is missing {error.args[0]}"
            ) from error


def _series_from_financial_output(output: Mapping[str, Any]) -> MarketDataSeries:
    if output.get("dataset") != "market.daily":
        raise TechnicalAnalysisError("technical analysis requires market.daily data")
    records = output.get("records")
    if not isinstance(records, list):
        raise TechnicalAnalysisError("market.daily records must be a list")
    normalized = []
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("fields"), Mapping):
            raise TechnicalAnalysisError("market.daily record shape is invalid")
        fields = record["fields"]
        normalized.append(
            {
                "symbol": record.get("subject"),
                "open": fields.get("open"),
                "high": fields.get("high"),
                "low": fields.get("low"),
                "close": fields.get("close"),
                "volume": fields.get("volume_shares"),
                "source": record.get("source"),
                "timestamp": record.get("timestamp"),
                "as_of": record.get("as_of"),
            }
        )
    return MarketDataSeries.from_records(normalized)


def _series_to_records(series: MarketDataSeries) -> list[dict[str, Any]]:
    return [
        {
            "symbol": bar.symbol,
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "volume": bar.volume,
            "source": bar.source,
            "timestamp": bar.timestamp.isoformat(),
            "as_of": bar.as_of.isoformat(),
        }
        for bar in series.bars
    ]


class _TechnicalMarketAnalysisTool:
    name = "technical_market_analysis"

    def __init__(
        self,
        financial_tool: FinancialDataTool,
        engine: TechnicalAnalysisEngine,
    ) -> None:
        self._financial_tool = financial_tool
        self._engine = engine

    def run(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = TechnicalAnalysisQuery.from_mapping(arguments)
        financial_output = self._financial_tool.run(
            {
                "dataset": "market.daily",
                "params": {
                    "symbol": query.symbol,
                    "start_date": query.start_date,
                    "end_date": query.end_date,
                    "limit": query.limit,
                },
                "mode": query.mode,
            }
        )
        series = _series_from_financial_output(financial_output)
        analysis = self._engine.analyze(series)
        return {
            "query": query.to_mapping(),
            "analysis": analysis.to_metadata(),
            "market_data": _series_to_records(series),
        }


def validate_technical_analysis_output(value: Any) -> CrossValidationResult:
    """Recompute every indicator and reject altered or invented values."""

    if not isinstance(value, Mapping):
        return CrossValidationResult(False, "technical output must be an object")
    records = value.get("market_data")
    analysis = value.get("analysis")
    if not isinstance(records, list) or not isinstance(analysis, Mapping):
        return CrossValidationResult(False, "technical output is incomplete")
    try:
        series = MarketDataSeries.from_records(records)
        expected = TechnicalAnalysisEngine().analyze(series).to_metadata()
    except Exception as error:
        return CrossValidationResult(False, f"cannot recompute analysis: {error}")
    if dict(analysis) != expected:
        return CrossValidationResult(False, "analysis does not match recomputed indicators")
    return CrossValidationResult(True)


class _TechnicalLoopAgent:
    name = "technical_analysis_loop"

    def create_plan(self, request: AgentRequest) -> Plan:
        TechnicalAnalysisQuery.from_mapping(request.context.get("technical_query", {}))
        return Plan(
            goal="produce a source-backed deterministic technical report",
            steps=("fetch daily bars", "calculate indicators", "verify report"),
        )

    def choose_action(self, state: CognitiveLoopState) -> Action:
        return Action(
            tool="technical_market_analysis",
            arguments=state.request.context["technical_query"],
            rationale="fetch validated bars and calculate all indicators deterministically",
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
                reason="the controlled data or calculation tool failed; retry within the hard limit",
            )
        output = observation.output
        if not isinstance(output, Mapping) or not isinstance(output.get("analysis"), Mapping):
            return Reflection(
                decision=ReflectionDecision.REVISE,
                reason="the observation shape is incomplete",
            )
        analysis = output["analysis"]
        return Reflection(
            decision=ReflectionDecision.COMPLETE,
            reason="indicators, provenance, schema, and deterministic cross-check all passed",
            final_answer=(
                f"{analysis['symbol']} 技术分析完成：趋势 {analysis['trend']}，"
                f"评分 {analysis['signal_score']}（{analysis['signal_label']}）。"
                "结果不构成投资建议。"
            ),
        )


@dataclass(frozen=True)
class TechnicalAnalysisWorkflowResult:
    """Convenient result while preserving the complete Loop evidence."""

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


class TechnicalAnalysisRuntime:
    """One deep interface for standalone and Graph technical analysis."""

    def __init__(
        self,
        financial_tool: FinancialDataTool,
        *,
        engine: TechnicalAnalysisEngine | None = None,
    ) -> None:
        self._engine = engine or TechnicalAnalysisEngine()
        self._tool = _TechnicalMarketAnalysisTool(financial_tool, self._engine)

    def run(self, query: TechnicalAnalysisQuery) -> TechnicalAnalysisWorkflowResult:
        if not isinstance(query, TechnicalAnalysisQuery):
            raise TechnicalAnalysisError("query must be a TechnicalAnalysisQuery")
        runner = CognitiveLoopRunner(
            agent=_TechnicalLoopAgent(),
            tools=ToolRegistry(
                [self._tool],
                agent_name=_TechnicalLoopAgent.name,
                permission_registry=build_default_agent_tool_policy_registry(),
            ),
            tool_guardrails=(
                JSONSchemaValidator(
                    output_schema=TECHNICAL_TOOL_OUTPUT_SCHEMA,
                    output_path="metadata.observation.output",
                    name="technical_output_schema",
                ),
                SourceAttributionFilter(
                    required_fields=("source", "timestamp", "as_of"),
                    output_paths="metadata.observation.output.market_data",
                    name="technical_market_sources",
                ),
                CrossValidator(
                    validate_technical_analysis_output,
                    output_path="metadata.observation.output",
                    name="technical_indicator_recompute",
                ),
            ),
            max_steps=2,
            max_tool_retries=0,
            memory_capacity=12,
        )
        loop_result = runner.run(
            AgentRequest(
                task=f"analyze technical indicators for {query.symbol}",
                context={"technical_query": query.to_mapping()},
            )
        )
        successful = [
            record.observation.output
            for record in loop_result.tool_records
            if record.observation.success
        ]
        if not successful or not isinstance(successful[-1], Mapping):
            raise TechnicalAnalysisError("technical loop produced no valid report")
        return TechnicalAnalysisWorkflowResult(successful[-1], loop_result)

    def run_graph_node(self, state: GraphState) -> Mapping[str, Any]:
        """Graph node adapter using the same runtime and verification path."""

        query = TechnicalAnalysisQuery.from_mapping(state["technical_query"])
        result = self.run(query).to_mapping()
        return {
            "technical_report": result["report"]["analysis"],
            "technical_evidence": result["report"]["market_data"],
            "technical_loop": result["loop"],
        }


def build_default_technical_analysis_runtime(
    *,
    project_root: str | Path | None = None,
    policy: FinancialDataPolicy | None = None,
) -> TechnicalAnalysisRuntime:
    root = Path(project_root) if project_root is not None else Path.cwd()
    hub = FinancialDataHub(
        live_provider=SubprocessFinancialDataProvider(),
        offline_provider=FixtureFinancialDataProvider(
            root / "tests" / "fixtures" / "technical_market_daily_30.json"
        ),
        cache=JsonFinancialDataCache(root / ".runtime" / "finance" / "data_cache.json"),
        policy=policy,
    )
    return TechnicalAnalysisRuntime(FinancialDataTool(hub))


__all__ = [
    "TECHNICAL_TOOL_OUTPUT_SCHEMA",
    "TechnicalAnalysisQuery",
    "TechnicalAnalysisRuntime",
    "TechnicalAnalysisWorkflowResult",
    "build_default_technical_analysis_runtime",
    "validate_technical_analysis_output",
]
