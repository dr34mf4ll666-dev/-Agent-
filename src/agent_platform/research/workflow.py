"""Composition root for the A5 non-financial research workflow."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from agent_platform.core import (
    AgentHarness,
    AgentRequest,
    CognitiveLoopRunner,
    CrossValidationResult,
    CrossValidator,
    GraphRunner,
    GraphState,
    GraphWorkflowLoader,
    JSONSchemaValidator,
    JsonCheckpointStore,
    ModelGateway,
    NodeRegistry,
    SourceAttributionFilter,
    ToolRegistry,
)

from .agents import GatewayResearchPlanner, GatewayResearchReporter
from .contracts import ResearchContractError, ResearchDocument, require_text, require_timestamp
from .tools import LocalDocumentSearchTool


SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["query", "result_count", "results"],
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "result_count": {"type": "integer", "minimum": 0},
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "document_id",
                    "title",
                    "excerpt",
                    "score",
                    "source",
                    "timestamp",
                    "as_of",
                ],
                "properties": {
                    "document_id": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "minLength": 1},
                    "excerpt": {"type": "string", "minLength": 1},
                    "score": {"type": "integer", "minimum": 1},
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

REPORT_SCHEMA = {
    "type": "object",
    "required": ["topic", "summary", "findings", "source", "timestamp"],
    "properties": {
        "topic": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "findings": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["claim", "evidence_ids"],
                "properties": {
                    "claim": {"type": "string", "minLength": 1},
                    "evidence_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
                "additionalProperties": False,
            },
        },
        "source": {"type": "string", "minLength": 1},
        "timestamp": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


def load_documents(path: str | Path) -> tuple[ResearchDocument, ...]:
    document_path = Path(path)
    try:
        payload = json.loads(document_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ResearchContractError(f"failed to load documents: {error}") from error
    if not isinstance(payload, list) or not payload:
        raise ResearchContractError("document file must contain a non-empty array")
    return tuple(ResearchDocument.from_mapping(item) for item in payload)


def organize_evidence(records: Any) -> list[dict[str, Any]]:
    if not isinstance(records, list) or not records:
        raise ResearchContractError("retrieval must contain at least one record")
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ResearchContractError("retrieval record must be an object")
        document_id = require_text(record.get("document_id"), "document_id")
        if document_id in seen:
            continue
        seen.add(document_id)
        evidence.append(
            {
                "evidence_id": f"E{len(evidence) + 1}",
                "document_id": document_id,
                "title": require_text(record.get("title"), "title"),
                "quote": require_text(record.get("excerpt"), "excerpt"),
                "score": record.get("score"),
                "source": require_text(record.get("source"), "source"),
                "timestamp": require_timestamp(record.get("timestamp"), "timestamp"),
                "as_of": require_timestamp(record.get("as_of"), "as_of"),
            }
        )
    if not evidence:
        raise ResearchContractError("no unique evidence remained after organization")
    return evidence


def validate_report_citations(
    report: Any,
    *,
    allowed_evidence_ids: set[str],
    expected_topic: str | None = None,
) -> CrossValidationResult:
    if not isinstance(report, Mapping):
        return CrossValidationResult(False, "report must be an object")
    if expected_topic is not None and report.get("topic") != expected_topic:
        return CrossValidationResult(False, "report topic does not match request")
    findings = report.get("findings")
    if not isinstance(findings, list) or not findings:
        return CrossValidationResult(False, "report must contain findings")
    for index, finding in enumerate(findings):
        if not isinstance(finding, Mapping):
            return CrossValidationResult(False, f"finding {index} must be an object")
        evidence_ids = finding.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            return CrossValidationResult(False, f"finding {index} has no evidence")
        unknown = sorted(set(evidence_ids) - allowed_evidence_ids)
        if unknown:
            return CrossValidationResult(
                False,
                f"finding {index} cites unknown evidence: {unknown}",
            )
    return CrossValidationResult(True)


class NonFinancialResearchRuntime:
    """Bind a new domain to existing platform seams without changing core code."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        documents: Iterable[ResearchDocument],
        workflow_path: str | Path,
        checkpoint_path: str | Path | None = None,
        fail_synthesis_attempts: int = 0,
    ) -> None:
        self._gateway = gateway
        self._documents = tuple(documents)
        self._workflow_path = Path(workflow_path)
        self._checkpoint_path = None if checkpoint_path is None else Path(checkpoint_path)
        if (
            isinstance(fail_synthesis_attempts, bool)
            or not isinstance(fail_synthesis_attempts, int)
            or fail_synthesis_attempts < 0
        ):
            raise ResearchContractError(
                "fail_synthesis_attempts must be a non-negative integer"
            )
        self._remaining_synthesis_failures = fail_synthesis_attempts
        self.node_calls = {"retrieve": 0, "organize": 0, "synthesize": 0}

    def run(
        self,
        *,
        topic: str,
        run_timestamp: str,
        resume: bool = False,
    ):
        normalized_topic = require_text(topic, "topic")
        normalized_timestamp = require_timestamp(run_timestamp, "run_timestamp")
        registry = NodeRegistry(
            {
                "research_retrieve": self._retrieve,
                "research_organize": self._organize,
                "research_synthesize": self._synthesize,
            }
        )
        graph = GraphWorkflowLoader(registry).load(self._workflow_path)
        checkpoint_store = (
            None
            if self._checkpoint_path is None
            else JsonCheckpointStore(self._checkpoint_path)
        )
        runner = GraphRunner(graph, checkpoint_store=checkpoint_store)
        return runner.run(
            {"topic": normalized_topic, "run_timestamp": normalized_timestamp},
            resume=resume,
        )

    def _retrieve(self, state: GraphState) -> Mapping[str, Any]:
        self.node_calls["retrieve"] += 1
        planner = GatewayResearchPlanner(self._gateway)
        search_tool = LocalDocumentSearchTool(self._documents)
        runner = CognitiveLoopRunner(
            agent=planner,
            tools=ToolRegistry([search_tool]),
            tool_guardrails=(
                JSONSchemaValidator(
                    output_schema=SEARCH_OUTPUT_SCHEMA,
                    output_path="metadata.observation.output",
                    name="research_search_schema",
                ),
                SourceAttributionFilter(
                    required_fields=("source", "timestamp", "as_of"),
                    output_paths="metadata.observation.output.results",
                    name="research_search_sources",
                ),
            ),
            max_steps=2,
            max_tool_retries=0,
            memory_capacity=12,
        )
        result = runner.run(AgentRequest(task=state["topic"]))
        successful = [
            record.observation.output
            for record in result.tool_records
            if record.observation.success
        ]
        if not successful:
            raise ResearchContractError("research loop produced no successful retrieval")
        search_output = successful[-1]
        if not isinstance(search_output, Mapping):
            raise ResearchContractError("search output must be an object")
        records = search_output.get("results")
        if not isinstance(records, list) or not records:
            raise ResearchContractError("local search returned no matching documents")
        return {
            "retrieval": {
                "query": search_output["query"],
                "records": records,
                "loop_steps": result.state.step_count,
                "allowed_tools": [search_tool.name],
                "working_memory_entries": len(result.state.memory.entries),
                "loop_trace": [
                    {
                        "event": event.event,
                        "step": event.step,
                        "attempt": event.attempt,
                        "detail": event.detail,
                    }
                    for event in result.trace
                ],
                "model_calls": planner.model_calls,
            }
        }

    def _organize(self, state: GraphState) -> Mapping[str, Any]:
        self.node_calls["organize"] += 1
        evidence = organize_evidence(state["retrieval"]["records"])
        return {"evidence": evidence, "evidence_count": len(evidence)}

    def _synthesize(self, state: GraphState) -> Mapping[str, Any]:
        self.node_calls["synthesize"] += 1
        if self._remaining_synthesis_failures > 0:
            self._remaining_synthesis_failures -= 1
            raise RuntimeError("simulated synthesis failure")

        evidence = state["evidence"]
        allowed_ids = {item["evidence_id"] for item in evidence}
        reporter = GatewayResearchReporter(self._gateway)
        harness = AgentHarness(
            reporter,
            guardrails=(
                JSONSchemaValidator(
                    output_schema=REPORT_SCHEMA,
                    output_path="metadata.report",
                    name="research_report_schema",
                ),
                SourceAttributionFilter(
                    output_paths="metadata.report",
                    name="research_report_source",
                ),
                CrossValidator(
                    lambda report: validate_report_citations(
                        report,
                        allowed_evidence_ids=allowed_ids,
                        expected_topic=state["topic"],
                    ),
                    output_path="metadata.report",
                    name="research_evidence_grounding",
                ),
            ),
        )
        result = harness.run(
            AgentRequest(
                task=f"synthesize local research: {state['topic']}",
                context={
                    "topic": state["topic"],
                    "run_timestamp": state["run_timestamp"],
                    "evidence": evidence,
                },
            )
        )
        return {
            "report": result.response.metadata["report"],
            "report_harness_trace": [
                {"event": event.event, "agent": event.agent, "detail": event.detail}
                for event in result.trace
            ],
            "report_model_calls": reporter.model_calls,
        }


__all__ = [
    "NonFinancialResearchRuntime",
    "REPORT_SCHEMA",
    "SEARCH_OUTPUT_SCHEMA",
    "load_documents",
    "organize_evidence",
    "validate_report_citations",
]
