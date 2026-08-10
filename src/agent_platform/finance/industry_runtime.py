"""B2 industry specialist runtime over Data Hub, Loop, Harness, and Graph."""

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
    FinancialDataHub,
    FinancialDataPolicy,
    FinancialDataTool,
    FixtureFinancialDataProvider,
    JsonFinancialDataCache,
    SubprocessFinancialDataProvider,
)
from .industry import IndustryAnalysisEngine, IndustryAnalysisError


INDUSTRY_DATASET_KEYS = {
    "industry_snapshot": "industry.snapshot",
    "policy_lpr": "macro.policy_lpr",
}


INDUSTRY_TOOL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["query", "analysis", "industry_data"],
    "properties": {
        "query": {
            "type": "object",
            "required": ["sector", "mode", "limit", "start_date", "end_date"],
            "properties": {
                "sector": {"type": "string", "minLength": 1},
                "mode": {"enum": ["offline", "live"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "start_date": {"type": "string", "minLength": 8, "maxLength": 8},
                "end_date": {"type": "string", "minLength": 8, "maxLength": 8},
            },
            "additionalProperties": False,
        },
        "analysis": {
            "type": "object",
            "required": [
                "sector",
                "as_of",
                "timestamp",
                "industry_profile",
                "competition",
                "policy",
                "prosperity",
                "industry_chain",
                "leaders",
                "score",
                "score_label",
                "score_components",
                "sources",
                "caveats",
            ],
            "properties": {
                "sector": {"type": "string", "minLength": 1},
                "as_of": {"type": "string", "minLength": 1},
                "timestamp": {"type": "string", "minLength": 1},
                "industry_profile": {"type": "object"},
                "competition": {"type": "object"},
                "policy": {"type": "object"},
                "prosperity": {"type": "object"},
                "industry_chain": {"type": "object"},
                "leaders": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "object"},
                },
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
        "industry_data": {
            "type": "object",
            "required": list(INDUSTRY_DATASET_KEYS),
            "properties": {key: {"type": "object"} for key in INDUSTRY_DATASET_KEYS},
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class IndustryAnalysisQuery:
    """Stable request understood by standalone and Graph callers."""

    sector: str = "玻璃行业"
    mode: str = "offline"
    limit: int = 5
    start_date: str = "20260101"
    end_date: str = "20260807"

    def __post_init__(self) -> None:
        if not isinstance(self.sector, str) or not self.sector.strip():
            raise IndustryAnalysisError("sector must be a non-empty string")
        object.__setattr__(self, "sector", self.sector.strip())
        if self.mode not in {"offline", "live"}:
            raise IndustryAnalysisError("mode must be offline or live")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 100
        ):
            raise IndustryAnalysisError("limit must be an integer from 1 to 100")
        try:
            start = int(self.start_date)
            end = int(self.end_date)
        except (TypeError, ValueError) as error:
            raise IndustryAnalysisError("start_date and end_date must use YYYYMMDD") from error
        if not re.fullmatch(r"\d{8}", self.start_date) or not re.fullmatch(
            r"\d{8}", self.end_date
        ):
            raise IndustryAnalysisError("start_date and end_date must use YYYYMMDD")
        if start > end:
            raise IndustryAnalysisError("start_date must not be later than end_date")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "sector": self.sector,
            "mode": self.mode,
            "limit": self.limit,
            "start_date": self.start_date,
            "end_date": self.end_date,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IndustryAnalysisQuery":
        if not isinstance(value, Mapping):
            raise IndustryAnalysisError("industry query must be an object")
        try:
            return cls(
                sector=value.get("sector", "玻璃行业"),
                mode=value.get("mode", "offline"),
                limit=value.get("limit", 5),
                start_date=value.get("start_date", "20260101"),
                end_date=value.get("end_date", "20260807"),
            )
        except KeyError as error:
            raise IndustryAnalysisError(
                f"industry query is missing {error.args[0]}"
            ) from error


def _bundle_for_engine(industry_data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(industry_data, Mapping):
        raise IndustryAnalysisError("industry_data must be an object")
    bundle = {}
    for key, dataset in INDUSTRY_DATASET_KEYS.items():
        if key not in industry_data:
            raise IndustryAnalysisError(f"industry_data is missing {key}")
        bundle[dataset.replace("industry.", "").replace("macro.", "")] = industry_data[key]
    return {
        "industry_snapshot": bundle["snapshot"],
        "policy_lpr": bundle["policy_lpr"],
    }


class _IndustryAnalysisTool:
    name = "industry_analysis"

    def __init__(
        self,
        financial_tool: FinancialDataTool,
        engine: IndustryAnalysisEngine,
    ) -> None:
        self._financial_tool = financial_tool
        self._engine = engine

    def run(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = IndustryAnalysisQuery.from_mapping(arguments)
        industry_data: dict[str, Any] = {}
        industry_data["industry_snapshot"] = self._financial_tool.run(
            {
                "dataset": "industry.snapshot",
                # The target sector may appear late in the provider's table.
                # Keep a broad comparison universe so arbitrary sectors do not
                # fail merely because the user requested a short policy history.
                "params": {"limit": max(query.limit, 50)},
                "mode": query.mode,
            }
        )
        industry_data["policy_lpr"] = self._financial_tool.run(
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
        analysis = self._engine.analyze(
            _bundle_for_engine(industry_data), sector=query.sector
        )
        return {
            "query": query.to_mapping(),
            "analysis": analysis,
            "industry_data": industry_data,
        }


def validate_industry_analysis_output(value: Any) -> CrossValidationResult:
    """Recompute industry metrics and reject altered conclusions."""

    if not isinstance(value, Mapping):
        return CrossValidationResult(False, "industry output must be an object")
    query = value.get("query")
    analysis = value.get("analysis")
    industry_data = value.get("industry_data")
    try:
        parsed_query = IndustryAnalysisQuery.from_mapping(query)
        expected = IndustryAnalysisEngine().analyze(
            _bundle_for_engine(industry_data), sector=parsed_query.sector
        )
    except Exception as error:
        return CrossValidationResult(False, f"cannot recompute industry analysis: {error}")
    if dict(analysis) != expected:
        return CrossValidationResult(False, "industry analysis does not match recomputed values")
    return CrossValidationResult(True)


class _IndustryLoopAgent:
    name = "industry_analysis_loop"

    def create_plan(self, request: AgentRequest) -> Plan:
        IndustryAnalysisQuery.from_mapping(request.context.get("industry_query", {}))
        return Plan(
            goal="produce a source-backed deterministic industry report",
            steps=(
                "fetch industry snapshot and policy data",
                "calculate prosperity, competition, chain, and leaders",
                "verify report",
            ),
        )

    def choose_action(self, state: CognitiveLoopState) -> Action:
        return Action(
            tool="industry_analysis",
            arguments=state.request.context["industry_query"],
            rationale="fetch controlled industry and policy datasets and calculate deterministic analysis",
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
                reason="the controlled industry data or calculation tool failed; retry within the hard limit",
            )
        output = observation.output
        if not isinstance(output, Mapping) or not isinstance(output.get("analysis"), Mapping):
            return Reflection(
                decision=ReflectionDecision.REVISE,
                reason="the industry observation shape is incomplete",
            )
        analysis = output["analysis"]
        return Reflection(
            decision=ReflectionDecision.COMPLETE,
            reason="industry profile, policy, leaders, provenance, schema, and deterministic cross-check passed",
            final_answer=(
                f"{analysis['sector']} 行业分析完成：景气度 {analysis['prosperity']['label']}，"
                f"评分 {analysis['score']}（{analysis['score_label']}）。结果不构成投资建议。"
            ),
        )


@dataclass(frozen=True)
class IndustryAnalysisWorkflowResult:
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


class IndustryAnalysisRuntime:
    """One deep interface for standalone and Graph industry analysis."""

    def __init__(
        self,
        financial_tool: FinancialDataTool,
        *,
        engine: IndustryAnalysisEngine | None = None,
    ) -> None:
        self._engine = engine or IndustryAnalysisEngine()
        self._tool = _IndustryAnalysisTool(financial_tool, self._engine)

    def run(self, query: IndustryAnalysisQuery) -> IndustryAnalysisWorkflowResult:
        if not isinstance(query, IndustryAnalysisQuery):
            raise IndustryAnalysisError("query must be an IndustryAnalysisQuery")
        source_paths = tuple(
            f"metadata.observation.output.industry_data.{key}.records"
            for key in INDUSTRY_DATASET_KEYS
        )
        runner = CognitiveLoopRunner(
            agent=_IndustryLoopAgent(),
            tools=ToolRegistry(
                [self._tool],
                agent_name=_IndustryLoopAgent.name,
                permission_registry=build_default_agent_tool_policy_registry(),
            ),
            tool_guardrails=(
                JSONSchemaValidator(
                    output_schema=INDUSTRY_TOOL_OUTPUT_SCHEMA,
                    output_path="metadata.observation.output",
                    name="industry_output_schema",
                ),
                SourceAttributionFilter(
                    required_fields=("source", "timestamp", "as_of"),
                    output_paths=source_paths,
                    name="industry_market_sources",
                ),
                CrossValidator(
                    validate_industry_analysis_output,
                    output_path="metadata.observation.output",
                    name="industry_value_recompute",
                ),
            ),
            max_steps=2,
            max_tool_retries=0,
            memory_capacity=12,
        )
        loop_result = runner.run(
            AgentRequest(
                task=f"analyze industry {query.sector}",
                context={"industry_query": query.to_mapping()},
            )
        )
        successful = [
            record.observation.output
            for record in loop_result.tool_records
            if record.observation.success
        ]
        if not successful or not isinstance(successful[-1], Mapping):
            raise IndustryAnalysisError("industry loop produced no valid report")
        return IndustryAnalysisWorkflowResult(successful[-1], loop_result)

    def run_graph_node(self, state: GraphState) -> Mapping[str, Any]:
        query = IndustryAnalysisQuery.from_mapping(state["industry_query"])
        result = self.run(query).to_mapping()
        return {
            "industry_report": result["report"]["analysis"],
            "industry_evidence": result["report"]["industry_data"],
            "industry_loop": result["loop"],
        }


def build_default_industry_analysis_runtime(
    *,
    project_root: str | Path | None = None,
    policy: FinancialDataPolicy | None = None,
) -> IndustryAnalysisRuntime:
    root = Path(project_root) if project_root is not None else Path.cwd()
    hub = FinancialDataHub(
        live_provider=SubprocessFinancialDataProvider(),
        offline_provider=FixtureFinancialDataProvider(
            root / "tests" / "fixtures" / "industry_analysis.json"
        ),
        cache=JsonFinancialDataCache(root / ".runtime" / "finance" / "data_cache.json"),
        policy=policy,
    )
    return IndustryAnalysisRuntime(FinancialDataTool(hub))


__all__ = [
    "INDUSTRY_DATASET_KEYS",
    "INDUSTRY_TOOL_OUTPUT_SCHEMA",
    "IndustryAnalysisQuery",
    "IndustryAnalysisRuntime",
    "IndustryAnalysisWorkflowResult",
    "build_default_industry_analysis_runtime",
    "validate_industry_analysis_output",
]
