import json
import tempfile
import unittest
from pathlib import Path

from agent_platform.core import (
    GraphExecutionError,
    JsonCheckpointStore,
    MockModelAdapter,
    ModelAdapterResponse,
    ModelGateway,
)
from agent_platform.research import (
    LocalDocumentSearchTool,
    NonFinancialResearchRuntime,
    ResearchContractError,
    ResearchDocument,
    build_offline_research_gateway,
    load_documents,
    validate_report_citations,
)


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ROOT / "tests" / "fixtures" / "research_documents.json"
WORKFLOW = ROOT / "Workflow" / "examples" / "non_financial_research.yaml"
TOPIC = "Python 代码评审实践"
TIMESTAMP = "2026-08-06T12:00:00+08:00"


class NonFinancialResearchTests(unittest.TestCase):
    def test_document_requires_timezone_and_provenance(self):
        with self.assertRaisesRegex(
            ResearchContractError,
            "timestamp must include a timezone",
        ):
            ResearchDocument(
                document_id="D1",
                title="title",
                content="content",
                source="local://test",
                timestamp="2026-08-06T12:00:00",
                as_of=TIMESTAMP,
            )

    def test_local_search_returns_ranked_provenance_records(self):
        tool = LocalDocumentSearchTool(load_documents(DOCUMENTS))

        output = tool.run({"query": "Python 代码评审", "limit": 3})

        self.assertGreaterEqual(output["result_count"], 1)
        self.assertEqual(output["results"][0]["document_id"], "DOC-001")
        self.assertTrue(output["results"][0]["source"].startswith("local://"))
        self.assertTrue(output["results"][0]["timestamp"].endswith("+08:00"))
        self.assertTrue(output["results"][0]["as_of"].endswith("+08:00"))

    def test_offline_workflow_reuses_platform_components(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = NonFinancialResearchRuntime(
                gateway=build_offline_research_gateway(TOPIC),
                documents=load_documents(DOCUMENTS),
                workflow_path=WORKFLOW,
                checkpoint_path=Path(directory) / "checkpoint.json",
            )

            result = runtime.run(topic=TOPIC, run_timestamp=TIMESTAMP)

        state = result.state.to_dict()
        self.assertEqual(
            result.execution_order,
            ("retrieve", "organize", "synthesize"),
        )
        self.assertEqual(set(result.statuses.values()), {"completed"})
        self.assertEqual(
            state["retrieval"]["allowed_tools"],
            ["local_document_search"],
        )
        self.assertEqual(state["retrieval"]["loop_steps"], 1)
        self.assertEqual(state["retrieval"]["working_memory_entries"], 4)
        self.assertEqual(len(state["retrieval"]["model_calls"]), 3)
        self.assertEqual(len(state["report_model_calls"]), 1)
        self.assertEqual(state["report"]["source"], "model:mock")
        self.assertEqual(
            state["report"]["findings"][0]["evidence_ids"],
            ["E1"],
        )
        harness_events = [item["event"] for item in state["report_harness_trace"]]
        self.assertIn("guardrail.output.passed", harness_events)
        self.assertIn("postflight.passed", harness_events)

    def test_checkpoint_resume_does_not_repeat_completed_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            gateway = build_offline_research_gateway(TOPIC)
            failing = NonFinancialResearchRuntime(
                gateway=gateway,
                documents=load_documents(DOCUMENTS),
                workflow_path=WORKFLOW,
                checkpoint_path=checkpoint,
                fail_synthesis_attempts=1,
            )

            with self.assertRaisesRegex(
                GraphExecutionError,
                "simulated synthesis failure",
            ):
                failing.run(topic=TOPIC, run_timestamp=TIMESTAMP)

            saved = JsonCheckpointStore(checkpoint).load()
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(
                saved.statuses,
                {
                    "retrieve": "completed",
                    "organize": "completed",
                    "synthesize": "failed",
                },
            )

            resumed = NonFinancialResearchRuntime(
                gateway=gateway,
                documents=load_documents(DOCUMENTS),
                workflow_path=WORKFLOW,
                checkpoint_path=checkpoint,
            )
            result = resumed.run(topic=TOPIC, run_timestamp=TIMESTAMP, resume=True)

        self.assertEqual(result.statuses["synthesize"], "completed")
        self.assertEqual(
            resumed.node_calls,
            {"retrieve": 0, "organize": 0, "synthesize": 1},
        )
        self.assertEqual(
            result.execution_order,
            ("retrieve", "organize", "synthesize"),
        )

    def test_report_cross_validation_rejects_unknown_evidence_and_topic_drift(self):
        unknown = validate_report_citations(
            {
                "topic": TOPIC,
                "findings": [{"claim": "x", "evidence_ids": ["E99"]}],
            },
            allowed_evidence_ids={"E1"},
            expected_topic=TOPIC,
        )
        drifted = validate_report_citations(
            {
                "topic": "另一个主题",
                "findings": [{"claim": "x", "evidence_ids": ["E1"]}],
            },
            allowed_evidence_ids={"E1"},
            expected_topic=TOPIC,
        )

        self.assertFalse(unknown.valid)
        self.assertIn("E99", unknown.detail)
        self.assertFalse(drifted.valid)
        self.assertIn("topic", drifted.detail)

    def test_model_cannot_select_a_tool_outside_the_allowlist(self):
        gateway = ModelGateway(
            MockModelAdapter(
                script=(
                    ModelAdapterResponse(
                        content="plan",
                        structured_output={
                            "goal": "research",
                            "steps": ["search"],
                        },
                        model="mock-research-v1",
                    ),
                    ModelAdapterResponse(
                        content="action",
                        structured_output={
                            "tool": "web_search",
                            "arguments": {"query": TOPIC, "limit": 3},
                            "rationale": "try an unregistered tool",
                        },
                        model="mock-research-v1",
                    ),
                )
            )
        )
        runtime = NonFinancialResearchRuntime(
            gateway=gateway,
            documents=load_documents(DOCUMENTS),
            workflow_path=WORKFLOW,
        )

        with self.assertRaises(GraphExecutionError) as caught:
            runtime.run(topic=TOPIC, run_timestamp=TIMESTAMP)

        self.assertIn("must equal 'local_document_search'", str(caught.exception))

    def test_fixture_loader_rejects_missing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "documents.json"
            invalid.write_text(
                json.dumps(
                    [
                        {
                            "document_id": "D1",
                            "title": "title",
                            "content": "content",
                            "timestamp": TIMESTAMP,
                            "as_of": TIMESTAMP,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ResearchContractError,
                "missing field: source",
            ):
                load_documents(invalid)


if __name__ == "__main__":
    unittest.main()
