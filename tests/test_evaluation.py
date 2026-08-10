import unittest

from agent_platform.core import (
    EvaluationCandidate,
    EvaluationContractError,
    EvaluationDataset,
    HarnessComparisonRunner,
    IndependentEvaluator,
)


DATASET = EvaluationDataset.from_mapping(
    {
        "name": "fixed",
        "version": 1,
        "cases": [
            {
                "case_id": "one",
                "task": "answer one fact",
                "expected_facts": {"answer": 42},
                "allowed_tools": ["calculator"],
                "forbidden_phrases": ["invented"],
            },
            {
                "case_id": "two",
                "task": "stay safe",
                "expected_facts": {"safe": True},
                "allowed_tools": [],
                "forbidden_phrases": [],
            },
        ],
    }
)


class IndependentEvaluatorTests(unittest.TestCase):
    def test_fixed_rules_score_facts_tools_forbidden_text_and_costs(self):
        report = IndependentEvaluator().evaluate(
            DATASET,
            (
                EvaluationCandidate(
                    "one",
                    "invented",
                    {"answer": 41, "extra": "x"},
                    executed_tools=("web",),
                    latency_ms=10,
                    total_tokens=5,
                ),
                EvaluationCandidate(
                    "two",
                    "safe",
                    {"safe": True},
                    latency_ms=30,
                    total_tokens=7,
                ),
            ),
        )

        self.assertEqual(report.summary["case_count"], 2)
        self.assertEqual(report.summary["invalid_api_calls"], 1)
        self.assertEqual(report.summary["end_to_end_success_rate_percent"], 50.0)
        self.assertEqual(report.summary["average_latency_ms"], 20.0)
        self.assertEqual(report.summary["total_tokens"], 12)
        self.assertEqual(report.cases[0].hallucinated_claims, 3)

    def test_candidate_ids_must_exactly_match_dataset(self):
        with self.assertRaisesRegex(EvaluationContractError, "exactly match"):
            IndependentEvaluator().evaluate(
                DATASET,
                (EvaluationCandidate("one", "ok", {"answer": 42}),),
            )

    def test_comparison_reports_all_required_deltas(self):
        baseline = (
            EvaluationCandidate("one", "invented", {"answer": 41}),
            EvaluationCandidate("two", "unsafe", {"safe": False}),
        )
        protected = (
            EvaluationCandidate(
                "one",
                "ok",
                {"answer": 42},
                executed_tools=("calculator",),
                recovery_attempted=True,
            ),
            EvaluationCandidate(
                "two", "safe", {"safe": True}, recovery_attempted=True
            ),
        )

        report = HarnessComparisonRunner().compare(
            DATASET,
            without_harness=baseline,
            with_harness=protected,
        )

        self.assertEqual(
            report.with_harness.summary["end_to_end_success_rate_percent"], 100.0
        )
        self.assertEqual(
            report.improvement["recovery_success_rate_percent"], 100.0
        )
        self.assertLess(report.improvement["hallucination_rate_change_points"], 0)

    def test_candidate_contract_rejects_non_boolean_completion_and_boolean_cost(self):
        with self.assertRaises(EvaluationContractError):
            EvaluationCandidate("one", "", {}, completed="yes")
        with self.assertRaises(EvaluationContractError):
            EvaluationCandidate("one", "", {}, latency_ms=True)


if __name__ == "__main__":
    unittest.main()
