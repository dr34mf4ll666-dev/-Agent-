"""独立于被评估 Agent 的固定数据集与确定性评分 module。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class EvaluationContractError(ValueError):
    """评估数据集、候选结果或对比条件无效。"""


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    task: str
    expected_facts: Mapping[str, Any]
    allowed_tools: tuple[str, ...] = ()
    forbidden_phrases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise EvaluationContractError("case_id must not be blank")
        if not isinstance(self.task, str) or not self.task.strip():
            raise EvaluationContractError("task must not be blank")
        if not isinstance(self.expected_facts, Mapping) or not self.expected_facts:
            raise EvaluationContractError("expected_facts must be a non-empty object")
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise EvaluationContractError("allowed_tools must be unique")
        if len(self.forbidden_phrases) != len(set(self.forbidden_phrases)):
            raise EvaluationContractError("forbidden_phrases must be unique")
        object.__setattr__(self, "expected_facts", deepcopy(dict(self.expected_facts)))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvaluationCase":
        required = {
            "case_id",
            "task",
            "expected_facts",
            "allowed_tools",
            "forbidden_phrases",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise EvaluationContractError(
                f"evaluation case keys must equal {sorted(required)!r}"
            )
        if not isinstance(value["allowed_tools"], list) or not isinstance(
            value["forbidden_phrases"], list
        ):
            raise EvaluationContractError(
                "allowed_tools and forbidden_phrases must be arrays"
            )
        return cls(
            case_id=value["case_id"],
            task=value["task"],
            expected_facts=value["expected_facts"],
            allowed_tools=tuple(value["allowed_tools"]),
            forbidden_phrases=tuple(value["forbidden_phrases"]),
        )


@dataclass(frozen=True)
class EvaluationDataset:
    name: str
    version: int
    cases: tuple[EvaluationCase, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvaluationDataset":
        if not isinstance(value, Mapping) or set(value) != {"name", "version", "cases"}:
            raise EvaluationContractError(
                "dataset must contain name, version, and cases"
            )
        if value["version"] != 1:
            raise EvaluationContractError("only evaluation dataset version 1 is supported")
        if not isinstance(value["name"], str) or not value["name"].strip():
            raise EvaluationContractError("dataset name must not be blank")
        if not isinstance(value["cases"], list) or not value["cases"]:
            raise EvaluationContractError("dataset cases must be a non-empty array")
        cases = tuple(EvaluationCase.from_mapping(item) for item in value["cases"])
        ids = [case.case_id for case in cases]
        if len(ids) != len(set(ids)):
            raise EvaluationContractError("dataset case_id values must be unique")
        return cls(name=value["name"].strip(), version=1, cases=cases)


@dataclass(frozen=True)
class EvaluationCandidate:
    case_id: str
    answer: str
    claims: Mapping[str, Any]
    executed_tools: tuple[str, ...] = ()
    completed: bool = True
    latency_ms: int = 0
    total_tokens: int = 0
    recovery_attempted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise EvaluationContractError("candidate case_id must not be blank")
        if not isinstance(self.answer, str):
            raise EvaluationContractError("candidate answer must be text")
        if not isinstance(self.claims, Mapping):
            raise EvaluationContractError("candidate claims must be an object")
        if not isinstance(self.completed, bool):
            raise EvaluationContractError("completed must be boolean")
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, int)
            or isinstance(self.total_tokens, bool)
            or not isinstance(self.total_tokens, int)
            or min(self.latency_ms, self.total_tokens) < 0
        ):
            raise EvaluationContractError("latency and tokens must be non-negative")
        object.__setattr__(self, "claims", deepcopy(dict(self.claims)))


@dataclass(frozen=True)
class EvaluationCaseResult:
    case_id: str
    score: float
    matched_facts: int
    expected_facts: int
    hallucinated_claims: int
    total_claims: int
    invalid_tool_calls: int
    completed: bool
    passed: bool
    recovery_attempted: bool
    recovery_succeeded: bool
    latency_ms: int
    total_tokens: int
    failures: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationReport:
    dataset: str
    summary: Mapping[str, Any]
    cases: tuple[EvaluationCaseResult, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "summary": deepcopy(dict(self.summary)),
            "cases": [
                {
                    "case_id": case.case_id,
                    "score": case.score,
                    "matched_facts": case.matched_facts,
                    "expected_facts": case.expected_facts,
                    "hallucinated_claims": case.hallucinated_claims,
                    "total_claims": case.total_claims,
                    "invalid_tool_calls": case.invalid_tool_calls,
                    "completed": case.completed,
                    "passed": case.passed,
                    "recovery_attempted": case.recovery_attempted,
                    "recovery_succeeded": case.recovery_succeeded,
                    "latency_ms": case.latency_ms,
                    "total_tokens": case.total_tokens,
                    "failures": list(case.failures),
                }
                for case in self.cases
            ],
        }


class IndependentEvaluator:
    """只使用固定答案和规则评分，不读取 Agent 的自评分。"""

    def evaluate(
        self,
        dataset: EvaluationDataset,
        candidates: Sequence[EvaluationCandidate],
    ) -> EvaluationReport:
        if not isinstance(dataset, EvaluationDataset):
            raise EvaluationContractError("dataset must be an EvaluationDataset")
        candidate_by_id: dict[str, EvaluationCandidate] = {}
        for candidate in candidates:
            if not isinstance(candidate, EvaluationCandidate):
                raise EvaluationContractError(
                    "candidates must contain EvaluationCandidate values"
                )
            if candidate.case_id in candidate_by_id:
                raise EvaluationContractError(
                    f"duplicate candidate case_id: {candidate.case_id}"
                )
            candidate_by_id[candidate.case_id] = candidate
        expected_ids = {case.case_id for case in dataset.cases}
        if set(candidate_by_id) != expected_ids:
            raise EvaluationContractError(
                "candidate case ids must exactly match the fixed dataset"
            )

        results = tuple(
            self._evaluate_case(case, candidate_by_id[case.case_id])
            for case in dataset.cases
        )
        total_claims = sum(result.total_claims for result in results)
        hallucinations = sum(result.hallucinated_claims for result in results)
        recovery_cases = [result for result in results if result.recovery_attempted]
        summary = {
            "case_count": len(results),
            "average_score": round(
                sum(result.score for result in results) / len(results), 2
            ),
            "hallucination_rate_percent": round(
                hallucinations / total_claims * 100, 2
            )
            if total_claims
            else 0.0,
            "invalid_api_calls": sum(
                result.invalid_tool_calls for result in results
            ),
            "end_to_end_success_rate_percent": round(
                sum(result.passed for result in results) / len(results) * 100, 2
            ),
            "average_latency_ms": round(
                sum(result.latency_ms for result in results) / len(results), 2
            ),
            "total_tokens": sum(result.total_tokens for result in results),
            "recovery_success_rate_percent": round(
                sum(result.recovery_succeeded for result in recovery_cases)
                / len(recovery_cases)
                * 100,
                2,
            )
            if recovery_cases
            else None,
        }
        return EvaluationReport(dataset=dataset.name, summary=summary, cases=results)

    @staticmethod
    def _evaluate_case(
        case: EvaluationCase,
        candidate: EvaluationCandidate,
    ) -> EvaluationCaseResult:
        failures: list[str] = []
        matched = 0
        hallucinations = 0
        for name, expected in case.expected_facts.items():
            if name not in candidate.claims:
                failures.append(f"missing fact: {name}")
            elif candidate.claims[name] == expected:
                matched += 1
            else:
                hallucinations += 1
                failures.append(f"incorrect fact: {name}")
        for name in candidate.claims:
            if name not in case.expected_facts:
                hallucinations += 1
                failures.append(f"unsupported fact: {name}")
        forbidden_hits = sum(
            phrase in candidate.answer for phrase in case.forbidden_phrases
        )
        if forbidden_hits:
            hallucinations += forbidden_hits
            failures.append("forbidden phrase present")
        invalid_tools = sum(
            tool not in case.allowed_tools for tool in candidate.executed_tools
        )
        if invalid_tools:
            failures.append("unauthorized tool was executed")
        if not candidate.completed:
            failures.append("execution did not complete")

        factual_ratio = matched / len(case.expected_facts)
        score = (
            factual_ratio * 60
            + (20 if candidate.completed else 0)
            + (10 if hallucinations == 0 else 0)
            + (10 if invalid_tools == 0 else 0)
        )
        passed = (
            candidate.completed
            and matched == len(case.expected_facts)
            and hallucinations == 0
            and invalid_tools == 0
        )
        return EvaluationCaseResult(
            case_id=case.case_id,
            score=round(score, 2),
            matched_facts=matched,
            expected_facts=len(case.expected_facts),
            hallucinated_claims=hallucinations,
            total_claims=len(candidate.claims) + forbidden_hits,
            invalid_tool_calls=invalid_tools,
            completed=candidate.completed,
            passed=passed,
            recovery_attempted=candidate.recovery_attempted,
            recovery_succeeded=candidate.recovery_attempted and passed,
            latency_ms=candidate.latency_ms,
            total_tokens=candidate.total_tokens,
            failures=tuple(failures),
        )


@dataclass(frozen=True)
class HarnessComparisonReport:
    without_harness: EvaluationReport
    with_harness: EvaluationReport
    improvement: Mapping[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "without_harness": self.without_harness.to_mapping(),
            "with_harness": self.with_harness.to_mapping(),
            "improvement": deepcopy(dict(self.improvement)),
        }


class HarnessComparisonRunner:
    """在同一固定数据集上比较有/无 Harness 的六项指标。"""

    def __init__(self, evaluator: IndependentEvaluator | None = None) -> None:
        self._evaluator = evaluator or IndependentEvaluator()

    def compare(
        self,
        dataset: EvaluationDataset,
        *,
        without_harness: Sequence[EvaluationCandidate],
        with_harness: Sequence[EvaluationCandidate],
    ) -> HarnessComparisonReport:
        baseline = self._evaluator.evaluate(dataset, without_harness)
        protected = self._evaluator.evaluate(dataset, with_harness)
        before = baseline.summary
        after = protected.summary
        return HarnessComparisonReport(
            without_harness=baseline,
            with_harness=protected,
            improvement={
                "hallucination_rate_change_points": round(
                    after["hallucination_rate_percent"]
                    - before["hallucination_rate_percent"],
                    2,
                ),
                "invalid_api_calls_change": (
                    after["invalid_api_calls"] - before["invalid_api_calls"]
                ),
                "success_rate_change_points": round(
                    after["end_to_end_success_rate_percent"]
                    - before["end_to_end_success_rate_percent"],
                    2,
                ),
                "average_latency_change_ms": round(
                    after["average_latency_ms"] - before["average_latency_ms"],
                    2,
                ),
                "token_cost_change": after["total_tokens"] - before["total_tokens"],
                "recovery_success_rate_percent": after[
                    "recovery_success_rate_percent"
                ],
            },
        )
