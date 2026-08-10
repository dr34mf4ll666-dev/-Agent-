"""B2 fundamental specialist runtime over Data Hub, Loop, Harness, and Graph."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
from .fundamental import DATASETS, FundamentalAnalysisEngine, FundamentalAnalysisError


DATASET_KEYS = {
    "balance_sheet": "fundamental.balance_sheet",
    "income_statement": "fundamental.income_statement",
    "cash_flow": "fundamental.cash_flow",
    "indicators": "fundamental.indicators",
    "valuation": "fundamental.valuation",
    "market_realtime": "market.realtime",
}

DERIVED_INDICATOR_SOURCE = "derived:akshare.stock_financial_report_sina"


FUNDAMENTAL_TOOL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["query", "analysis", "fundamental_data"],
    "properties": {
        "query": {
            "type": "object",
            "required": ["symbol", "mode", "limit", "start_year"],
            "properties": {
                "symbol": {"type": "string", "minLength": 8},
                "mode": {"enum": ["offline", "live"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                "start_year": {"type": "string", "minLength": 4, "maxLength": 4},
            },
            "additionalProperties": False,
        },
        "analysis": {
            "type": "object",
            "required": [
                "symbol",
                "as_of",
                "timestamp",
                "statements",
                "indicators",
                "valuation",
                "growth",
                "dcf",
                "score",
                "score_label",
                "score_components",
                "sources",
                "caveats",
            ],
            "properties": {
                "symbol": {"type": "string", "minLength": 1},
                "as_of": {"type": "string", "minLength": 1},
                "timestamp": {"type": "string", "minLength": 1},
                "latest_financial_period": {"type": "string", "minLength": 1},
                "annual_base_period": {"type": "string", "minLength": 1},
                "statements": {"type": "object"},
                "indicators": {"type": "object"},
                "valuation": {"type": "object"},
                "growth": {"type": "object"},
                "dcf": {"type": "object"},
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
                    "minItems": 6,
                    "maxItems": 6,
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
        "fundamental_data": {
            "type": "object",
            "required": list(DATASET_KEYS),
            "properties": {key: {"type": "object"} for key in DATASET_KEYS},
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class FundamentalAnalysisQuery:
    """Stable request understood by standalone and Graph callers."""

    symbol: str
    mode: str = "offline"
    limit: int = 4
    start_year: str = "2024"

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not re.fullmatch(
            r"(?:sh|sz|bj)\d{6}", self.symbol.lower()
        ):
            raise FundamentalAnalysisError(
                "symbol must include a market prefix, for example sz000001"
            )
        object.__setattr__(self, "symbol", self.symbol.lower())
        if self.mode not in {"offline", "live"}:
            raise FundamentalAnalysisError("mode must be offline or live")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 12
        ):
            raise FundamentalAnalysisError("limit must be an integer from 1 to 12")
        if not isinstance(self.start_year, str) or not re.fullmatch(
            r"\d{4}", self.start_year
        ):
            raise FundamentalAnalysisError("start_year must use YYYY")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mode": self.mode,
            "limit": self.limit,
            "start_year": self.start_year,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FundamentalAnalysisQuery":
        if not isinstance(value, Mapping):
            raise FundamentalAnalysisError("fundamental query must be an object")
        try:
            return cls(
                symbol=value["symbol"],
                mode=value.get("mode", "offline"),
                limit=value.get("limit", 4),
                start_year=value.get("start_year", "2024"),
            )
        except KeyError as error:
            raise FundamentalAnalysisError(
                f"fundamental query is missing {error.args[0]}"
            ) from error


def _bundle_for_engine(fundamental_data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(fundamental_data, Mapping):
        raise FundamentalAnalysisError("fundamental_data must be an object")
    bundle = {}
    for key, dataset in DATASET_KEYS.items():
        if key not in fundamental_data:
            raise FundamentalAnalysisError(f"fundamental_data is missing {key}")
        bundle[dataset] = fundamental_data[key]
    return bundle


def _record_decimal(record: Mapping[str, Any], *names: str) -> Decimal:
    fields = record.get("fields")
    if not isinstance(fields, Mapping):
        raise FundamentalAnalysisError("derived indicator source is missing fields")
    for name in names:
        value = fields.get(name)
        if value is None:
            continue
        try:
            return Decimal(str(value))
        except InvalidOperation as error:
            raise FundamentalAnalysisError(
                f"derived indicator field is not numeric: {name}"
            ) from error
    raise FundamentalAnalysisError(
        f"derived indicator source is missing one of {names!r}"
    )


def _latest_records(dataset: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records = dataset.get("records")
    if not isinstance(records, list) or not records:
        raise FundamentalAnalysisError("derived indicator source has no records")
    if any(not isinstance(record, Mapping) for record in records):
        raise FundamentalAnalysisError("derived indicator records must be objects")
    return sorted(records, key=lambda record: str(record.get("as_of", "")), reverse=True)


def _same_period_last_year(
    latest: Mapping[str, Any],
    records: list[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    try:
        latest_date = datetime.fromisoformat(str(latest["as_of"]))
    except (KeyError, TypeError, ValueError) as error:
        raise FundamentalAnalysisError(
            "derived indicator source has an invalid as_of"
        ) from error
    for record in records[1:]:
        try:
            candidate = datetime.fromisoformat(str(record["as_of"]))
        except (KeyError, TypeError, ValueError):
            continue
        if (
            candidate.year == latest_date.year - 1
            and candidate.month == latest_date.month
            and candidate.day == latest_date.day
        ):
            return record
    return None


def derive_indicators_from_statements(
    fundamental_data: Mapping[str, Any],
    *,
    symbol: str,
    mode: str,
) -> dict[str, Any]:
    """Build transparent ratio proxies when the external indicator page fails."""

    balance_records = _latest_records(fundamental_data["balance_sheet"])
    income_records = _latest_records(fundamental_data["income_statement"])
    balance = balance_records[0]
    income = income_records[0]
    prior_income = _same_period_last_year(income, income_records)
    assets = _record_decimal(balance, "资产总计")
    liabilities = _record_decimal(balance, "负债合计")
    equity = _record_decimal(
        balance,
        "归属于母公司股东的权益",
        "归属于母公司所有者权益",
    )
    net_profit = _record_decimal(income, "归属于母公司的净利润", "净利润")
    if assets <= 0 or equity <= 0:
        raise FundamentalAnalysisError(
            "cannot derive indicators from non-positive assets or equity"
        )
    as_of = min(str(balance["as_of"]), str(income["as_of"]))
    if prior_income is None:
        profit_growth = Decimal("0")
        growth_basis = "same-period prior-year unavailable; neutral proxy used"
    else:
        prior_profit = _record_decimal(
            prior_income,
            "归属于母公司的净利润",
            "净利润",
        )
        profit_growth = (
            (net_profit - prior_profit) / abs(prior_profit) * Decimal("100")
            if prior_profit
            else Decimal("0")
        )
        growth_basis = "same-period prior-year statement comparison"
    timestamp = max(str(balance["timestamp"]), str(income["timestamp"]))
    fields = {
        "roe_percent": str(net_profit / equity * Decimal("100")),
        "weighted_roe_percent": str(net_profit / equity * Decimal("100")),
        "debt_to_asset_percent": str(liabilities / assets * Decimal("100")),
        "return_on_assets_percent": str(net_profit / assets * Decimal("100")),
        "net_profit_growth_percent": str(profit_growth),
        "total_assets_cny": str(assets),
        "derivation": "period-to-date statement ratio proxies",
        "growth_basis": growth_basis,
    }
    record = {
        "subject": symbol,
        "fields": fields,
        "source": DERIVED_INDICATOR_SOURCE,
        "timestamp": timestamp,
        "as_of": as_of,
    }
    return {
        "dataset": "fundamental.indicators",
        "record_count": 1,
        "source": DERIVED_INDICATOR_SOURCE,
        "timestamp": timestamp,
        "attempts": 0,
        "cache_hit": False,
        "mode": mode,
        "records": [record],
        "trace": [
            {
                "event": "provider.fallback.derived",
                "attempt": 0,
                "detail": "external indicator page failed; ratios derived from statements",
            }
        ],
    }


class _FundamentalAnalysisTool:
    name = "fundamental_analysis"

    def __init__(
        self,
        financial_tool: FinancialDataTool,
        engine: FundamentalAnalysisEngine,
    ) -> None:
        self._financial_tool = financial_tool
        self._engine = engine

    def run(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = FundamentalAnalysisQuery.from_mapping(arguments)
        fundamental_data: dict[str, Any] = {}
        for key, dataset in DATASET_KEYS.items():
            params: dict[str, Any] = {
                "symbol": query.symbol,
                "limit": query.limit,
            }
            if dataset == "fundamental.indicators":
                params["start_year"] = query.start_year
            try:
                fundamental_data[key] = self._financial_tool.run(
                    {
                        "dataset": dataset,
                        "params": params,
                        "mode": query.mode,
                    }
                )
            except FinancialDataError as error:
                can_derive = (
                    dataset == "fundamental.indicators"
                    and query.mode == "live"
                    and error.code
                    in {
                        FinancialDataErrorCode.PROVIDER_UNAVAILABLE,
                        FinancialDataErrorCode.EMPTY_RESPONSE,
                        FinancialDataErrorCode.TIMEOUT,
                    }
                    and "balance_sheet" in fundamental_data
                    and "income_statement" in fundamental_data
                )
                if not can_derive:
                    raise
                fundamental_data[key] = derive_indicators_from_statements(
                    fundamental_data,
                    symbol=query.symbol,
                    mode=query.mode,
                )
        analysis = self._engine.analyze(
            _bundle_for_engine(fundamental_data),
            symbol=query.symbol,
        )
        return {
            "query": query.to_mapping(),
            "analysis": analysis,
            "fundamental_data": fundamental_data,
        }


def validate_fundamental_analysis_output(value: Any) -> CrossValidationResult:
    """Recompute all fundamental metrics and reject altered conclusions."""

    if not isinstance(value, Mapping):
        return CrossValidationResult(False, "fundamental output must be an object")
    query = value.get("query")
    analysis = value.get("analysis")
    fundamental_data = value.get("fundamental_data")
    try:
        parsed_query = FundamentalAnalysisQuery.from_mapping(query)
        expected = FundamentalAnalysisEngine().analyze(
            _bundle_for_engine(fundamental_data),
            symbol=parsed_query.symbol,
        )
    except Exception as error:
        return CrossValidationResult(False, f"cannot recompute fundamental analysis: {error}")
    if dict(analysis) != expected:
        return CrossValidationResult(False, "fundamental analysis does not match recomputed values")
    return CrossValidationResult(True)


class _FundamentalLoopAgent:
    name = "fundamental_analysis_loop"

    def create_plan(self, request: AgentRequest) -> Plan:
        FundamentalAnalysisQuery.from_mapping(request.context.get("fundamental_query", {}))
        return Plan(
            goal="produce a source-backed deterministic fundamental report",
            steps=("fetch statements and indicators", "calculate valuation and DCF", "verify report"),
        )

    def choose_action(self, state: CognitiveLoopState) -> Action:
        return Action(
            tool="fundamental_analysis",
            arguments=state.request.context["fundamental_query"],
            rationale="fetch controlled financial datasets and calculate deterministic fundamentals",
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
                reason="the controlled financial data or calculation tool failed; retry within the hard limit",
            )
        output = observation.output
        if not isinstance(output, Mapping) or not isinstance(output.get("analysis"), Mapping):
            return Reflection(
                decision=ReflectionDecision.REVISE,
                reason="the fundamental observation shape is incomplete",
            )
        analysis = output["analysis"]
        return Reflection(
            decision=ReflectionDecision.COMPLETE,
            reason="statements, provenance, valuation, DCF, schema, and deterministic cross-check passed",
            final_answer=(
                f"{analysis['symbol']} 基本面分析完成：评分 {analysis['score']} "
                f"（{analysis['score_label']}），估值分位 {analysis['valuation']['valuation_percentile']}%。"
                "结果不构成投资建议。"
            ),
        )


@dataclass(frozen=True)
class FundamentalAnalysisWorkflowResult:
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


class FundamentalAnalysisRuntime:
    """One deep interface for standalone and Graph fundamental analysis."""

    def __init__(
        self,
        financial_tool: FinancialDataTool,
        *,
        engine: FundamentalAnalysisEngine | None = None,
    ) -> None:
        self._engine = engine or FundamentalAnalysisEngine()
        self._tool = _FundamentalAnalysisTool(financial_tool, self._engine)

    def run(self, query: FundamentalAnalysisQuery) -> FundamentalAnalysisWorkflowResult:
        if not isinstance(query, FundamentalAnalysisQuery):
            raise FundamentalAnalysisError("query must be a FundamentalAnalysisQuery")
        source_paths = tuple(
            f"metadata.observation.output.fundamental_data.{key}.records"
            for key in DATASET_KEYS
        )
        runner = CognitiveLoopRunner(
            agent=_FundamentalLoopAgent(),
            tools=ToolRegistry([self._tool]),
            tool_guardrails=(
                JSONSchemaValidator(
                    output_schema=FUNDAMENTAL_TOOL_OUTPUT_SCHEMA,
                    output_path="metadata.observation.output",
                    name="fundamental_output_schema",
                ),
                SourceAttributionFilter(
                    required_fields=("source", "timestamp", "as_of"),
                    output_paths=source_paths,
                    name="fundamental_market_sources",
                ),
                CrossValidator(
                    validate_fundamental_analysis_output,
                    output_path="metadata.observation.output",
                    name="fundamental_value_recompute",
                ),
            ),
            max_steps=2,
            max_tool_retries=0,
            memory_capacity=12,
        )
        loop_result = runner.run(
            AgentRequest(
                task=f"analyze fundamentals for {query.symbol}",
                context={"fundamental_query": query.to_mapping()},
            )
        )
        successful = [
            record.observation.output
            for record in loop_result.tool_records
            if record.observation.success
        ]
        if not successful or not isinstance(successful[-1], Mapping):
            raise FundamentalAnalysisError("fundamental loop produced no valid report")
        return FundamentalAnalysisWorkflowResult(successful[-1], loop_result)

    def run_graph_node(self, state: GraphState) -> Mapping[str, Any]:
        query = FundamentalAnalysisQuery.from_mapping(state["fundamental_query"])
        result = self.run(query).to_mapping()
        return {
            "fundamental_report": result["report"]["analysis"],
            "fundamental_evidence": result["report"]["fundamental_data"],
            "fundamental_loop": result["loop"],
        }


def build_default_fundamental_analysis_runtime(
    *,
    project_root: str | Path | None = None,
    policy: FinancialDataPolicy | None = None,
) -> FundamentalAnalysisRuntime:
    root = Path(project_root) if project_root is not None else Path.cwd()
    hub = FinancialDataHub(
        live_provider=SubprocessFinancialDataProvider(),
        offline_provider=FixtureFinancialDataProvider(
            root / "tests" / "fixtures" / "fundamental_analysis.json"
        ),
        cache=JsonFinancialDataCache(root / ".runtime" / "finance" / "data_cache.json"),
        policy=policy,
    )
    return FundamentalAnalysisRuntime(FinancialDataTool(hub))


__all__ = [
    "DATASET_KEYS",
    "FUNDAMENTAL_TOOL_OUTPUT_SCHEMA",
    "FundamentalAnalysisQuery",
    "FundamentalAnalysisRuntime",
    "FundamentalAnalysisWorkflowResult",
    "build_default_fundamental_analysis_runtime",
    "validate_fundamental_analysis_output",
]
