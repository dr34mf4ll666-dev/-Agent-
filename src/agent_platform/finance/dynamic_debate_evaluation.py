"""Fixed, repeatable evaluation for constrained dynamic financial debate."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_platform.core import (
    DeepSeekChatAdapter,
    ModelGateway,
    ModelRequest,
    ModelRetryPolicy,
    ModelUsage,
)

from .combined_analysis import CombinedAnalysisQuery, build_default_combined_analysis_runtime
from .dynamic_debate import DynamicDebateRuntime, ModelGatewayPort
from .structured_debate import (
    StructuredDebateQuery,
    StructuredDebateRuntime,
    validate_structured_debate,
)


DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[1] / "resources" / "dynamic_debate_evaluation.json"
)


@dataclass(frozen=True)
class DynamicDebateEvaluationReport:
    dataset: Mapping[str, Any]
    baseline: Mapping[str, Any]
    dynamic: Mapping[str, Any]
    raw_results: tuple[Mapping[str, Any], ...]
    acceptance: Mapping[str, bool]
    provider: str
    model: str
    live: bool
    passed: bool

    def to_mapping(self) -> dict[str, Any]:
        return {
            "dataset": deepcopy(dict(self.dataset)),
            "baseline": deepcopy(dict(self.baseline)),
            "dynamic": deepcopy(dict(self.dynamic)),
            "raw_results": [deepcopy(dict(item)) for item in self.raw_results],
            "acceptance": deepcopy(dict(self.acceptance)),
            "provider": self.provider,
            "model": self.model,
            "live": self.live,
            "passed": self.passed,
            "methodology": {
                "candidate_evidence_validity_rate": "accepted model candidates / all semantic candidate attempts",
                "final_evidence_validity_rate": "final reports passing deterministic replay / all runs",
                "viewpoint_diversity_rate": "unique Claim and Reasoning signatures / all runs",
                "bull_bear_balance_rate": "runs where both sides cover at least two specialists / all runs",
                "retry_rate": "runs requiring more than one semantic attempt / all runs",
                "fallback_rate": "runs ending in deterministic fallback / all runs",
                "result_stability_rate": "repeated runs preserving final validity, balance, and safety / all runs",
            },
            "safety": {
                "candidate_language_only": True,
                "deterministic_evidence_validation": True,
                "changes_synthesis": False,
                "changes_risk_controls": False,
                "real_trading_allowed": False,
            },
        }


class DynamicDebateEvaluationRuntime:
    """One interface hiding dataset loading, repeated runs, and all statistics."""

    def __init__(
        self,
        *,
        project_root: Path,
        gateway: ModelGatewayPort,
        provider: str,
        model: str,
        live: bool,
    ) -> None:
        self._project_root = project_root.resolve()
        self._gateway = gateway
        self._provider = provider
        self._model = model
        self._live = live

    @classmethod
    def from_project(
        cls,
        project_root: str | Path | None = None,
        *,
        live: bool = False,
        model: str = "deepseek-v4-flash",
    ) -> "DynamicDebateEvaluationRuntime":
        root = Path(project_root or Path(__file__).resolve().parents[3]).resolve()
        if live:
            gateway: ModelGatewayPort = ModelGateway(
                DeepSeekChatAdapter.from_env(model=model),
                retry_policy=ModelRetryPolicy(
                    max_attempts=2,
                    timeout_seconds=30,
                    initial_backoff_seconds=0.25,
                ),
            )
            return cls(
                project_root=root,
                gateway=gateway,
                provider="deepseek",
                model=model,
                live=True,
            )
        return cls(
            project_root=root,
            gateway=_EvaluationScriptedGateway(),
            provider="scripted_mock",
            model="dynamic-debate-eval-v1",
            live=False,
        )

    def run(
        self,
        dataset: Mapping[str, Any] | str | Path | None = None,
    ) -> DynamicDebateEvaluationReport:
        definition = _load_dataset(dataset)
        cases = _validate_dataset(definition)
        thresholds = _validate_thresholds(definition)
        combined = build_default_combined_analysis_runtime(
            project_root=self._project_root
        ).run(CombinedAnalysisQuery.for_symbol()).to_mapping()["report"]
        deterministic = StructuredDebateRuntime()
        dynamic = DynamicDebateRuntime(
            gateway=self._gateway,
            max_semantic_attempts=int(
                definition.get("model_config", {}).get("max_semantic_attempts", 2)
            ),
        )
        raw: list[dict[str, Any]] = []
        baseline_signatures: set[str] = set()
        dynamic_signatures: set[str] = set()
        for case in cases:
            for repetition in range(1, case["repetitions"] + 1):
                query = StructuredDebateQuery(combined, rounds=case["rounds"])
                baseline_result = deterministic.run(query).report
                dynamic_result = dynamic.run(query).to_mapping()
                baseline_valid = validate_structured_debate(
                    baseline_result, combined
                ).valid
                dynamic_valid = validate_structured_debate(
                    dynamic_result["report"], combined
                ).valid
                baseline_balanced = _balanced(baseline_result)
                dynamic_balanced = _balanced(dynamic_result["report"])
                baseline_signature = _language_signature(baseline_result)
                dynamic_signature = _language_signature(dynamic_result["report"])
                baseline_signatures.add(baseline_signature)
                dynamic_signatures.add(dynamic_signature)
                rejected_candidates = sum(
                    1
                    for event in dynamic_result["trace"]
                    if event.get("event") == "dynamic_debate.model_candidate.rejected"
                )
                raw.append(
                    {
                        "case_id": case["id"],
                        "rounds": case["rounds"],
                        "repetition": repetition,
                        "baseline": {
                            "final_evidence_valid": baseline_valid,
                            "bull_bear_balanced": baseline_balanced,
                            "language_signature": baseline_signature,
                            "tokens": 0,
                            "latency_ms": 0,
                            "report": baseline_result,
                        },
                        "dynamic": {
                            "mode": dynamic_result["mode"],
                            "semantic_attempts": dynamic_result["semantic_attempts"],
                            "rejected_candidates": rejected_candidates,
                            "final_evidence_valid": dynamic_valid,
                            "bull_bear_balanced": dynamic_balanced,
                            "language_signature": dynamic_signature,
                            "fallback_reason": dynamic_result["fallback_reason"],
                            "tokens": dynamic_result["usage"]["total_tokens"],
                            "latency_ms": dynamic_result["latency_ms"],
                            "safety_valid": _safety_valid(dynamic_result["safety"]),
                            "report": dynamic_result["report"],
                            "trace": dynamic_result["trace"],
                        },
                    }
                )
        run_count = len(raw)
        baseline_metrics = _baseline_metrics(raw, baseline_signatures)
        dynamic_metrics = _dynamic_metrics(raw, dynamic_signatures)
        acceptance = {
            "候选证据有效率达到阈值": dynamic_metrics[
                "candidate_evidence_validity_rate_percent"
            ] >= thresholds["minimum_candidate_evidence_validity_rate_percent"],
            "最终证据有效率达到阈值": dynamic_metrics[
                "final_evidence_validity_rate_percent"
            ] >= thresholds["minimum_final_evidence_validity_rate_percent"],
            "正反平衡率达到阈值": dynamic_metrics[
                "bull_bear_balance_rate_percent"
            ] >= thresholds["minimum_bull_bear_balance_rate_percent"],
            "重试率不超过阈值": dynamic_metrics["retry_rate_percent"]
            <= thresholds["maximum_retry_rate_percent"],
            "降级率不超过阈值": dynamic_metrics["fallback_rate_percent"]
            <= thresholds["maximum_fallback_rate_percent"],
            "结果稳定性达到阈值": dynamic_metrics[
                "result_stability_rate_percent"
            ] >= thresholds["minimum_result_stability_rate_percent"],
            "动态观点多样性高于模板基线": (
                not thresholds["dynamic_diversity_must_exceed_baseline"]
                or dynamic_metrics["viewpoint_diversity_rate_percent"]
                > baseline_metrics["viewpoint_diversity_rate_percent"]
            ),
            "所有运行保持交易安全边界": all(
                item["dynamic"]["safety_valid"] for item in raw
            ),
        }
        return DynamicDebateEvaluationReport(
            dataset={
                "version": definition["version"],
                "name": definition["name"],
                "description": definition.get("description", ""),
                "case_count": len(cases),
                "run_count": run_count,
            },
            baseline=baseline_metrics,
            dynamic=dynamic_metrics,
            raw_results=tuple(raw),
            acceptance=acceptance,
            provider=self._provider,
            model=self._model,
            live=self._live,
            passed=all(acceptance.values()),
        )


class _EvaluationScriptedGateway:
    """Deterministic adapter that includes one recoverable bad first candidate."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: ModelRequest) -> Any:
        self.calls += 1
        context = json.loads(request.prompt)
        catalog = context["evidence_catalog"]

        def ids(*specialists: str) -> list[str]:
            return [
                next(item["id"] for item in catalog if item["specialist"] == name)
                for name in specialists
            ]

        bull_ids = ids("fundamental", "industry")
        bear_ids = ids("technical", "macro")
        if self.calls == 1:
            bull_ids = ["E999", "E998"]
        variants = ("稳健", "审慎", "均衡", "克制", "谨慎")
        variant = variants[(self.calls - 1) % len(variants)]
        rounds = []
        for number in range(1, int(context["round_count"]) + 1):
            rounds.append(
                {
                    "round": number,
                    "bull": {
                        "claim": f"{variant}看多观点强调经营与行业证据",
                        "evidence_ids": bull_ids,
                        "reasoning": f"{variant}地组合基本面和行业证据，不延伸为交易指令。",
                    },
                    "bear": {
                        "claim": f"{variant}风险观点强调趋势与市场环境",
                        "evidence_ids": bear_ids,
                        "reasoning": f"{variant}地组合技术和宏观证据，保留反向约束。",
                    },
                }
            )
        output = {"rounds": rounds}
        response = type(
            "EvaluationResponse",
            (),
            {
                "structured_output": output,
                "provider": "scripted_mock",
                "model": "dynamic-debate-eval-v1",
                "usage": ModelUsage(input_tokens=120, output_tokens=80, total_tokens=200),
                "latency_ms": 25,
            },
        )()
        return type("EvaluationGatewayResult", (), {"response": response})()


def _load_dataset(dataset: Mapping[str, Any] | str | Path | None) -> dict[str, Any]:
    if dataset is None:
        path = DEFAULT_DATASET_PATH
        return json.loads(path.read_text(encoding="utf-8"))
    if isinstance(dataset, Mapping):
        return deepcopy(dict(dataset))
    return json.loads(Path(dataset).read_text(encoding="utf-8"))


def _validate_dataset(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    if definition.get("version") != 1:
        raise ValueError("dynamic debate evaluation dataset version must be 1")
    if not isinstance(definition.get("name"), str) or not definition["name"].strip():
        raise ValueError("dynamic debate evaluation dataset name is required")
    cases = definition.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("dynamic debate evaluation cases are required")
    checked = []
    seen = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("dynamic debate evaluation case must be an object")
        case_id = case.get("id")
        rounds = case.get("rounds")
        repetitions = case.get("repetitions")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError("dynamic debate evaluation case id must be unique")
        if rounds not in (2, 3):
            raise ValueError("dynamic debate evaluation rounds must be 2 or 3")
        if isinstance(repetitions, bool) or not isinstance(repetitions, int) or not 1 <= repetitions <= 10:
            raise ValueError("dynamic debate evaluation repetitions must be from 1 to 10")
        seen.add(case_id)
        checked.append({"id": case_id, "rounds": rounds, "repetitions": repetitions})
    return checked


def _validate_thresholds(definition: Mapping[str, Any]) -> dict[str, Any]:
    thresholds = definition.get("acceptance_thresholds")
    required = (
        "minimum_candidate_evidence_validity_rate_percent",
        "minimum_final_evidence_validity_rate_percent",
        "minimum_bull_bear_balance_rate_percent",
        "maximum_retry_rate_percent",
        "maximum_fallback_rate_percent",
        "minimum_result_stability_rate_percent",
    )
    if not isinstance(thresholds, Mapping):
        raise ValueError("dynamic debate evaluation acceptance thresholds are required")
    checked: dict[str, Any] = {}
    for name in required:
        value = thresholds.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= value <= 100
        ):
            raise ValueError(
                f"dynamic debate evaluation threshold {name} must be from 0 to 100"
            )
        checked[name] = float(value)
    diversity = thresholds.get("dynamic_diversity_must_exceed_baseline")
    if not isinstance(diversity, bool):
        raise ValueError("dynamic diversity comparison threshold must be boolean")
    checked["dynamic_diversity_must_exceed_baseline"] = diversity
    return checked


def _language_signature(report: Mapping[str, Any]) -> str:
    language = [
        (item[side]["claim"], item[side]["reasoning"])
        for item in report["rounds"]
        for side in ("bull", "bear")
    ]
    return json.dumps(language, ensure_ascii=False, separators=(",", ":"))


def _balanced(report: Mapping[str, Any]) -> bool:
    value = report.get("evidence_balance", {})
    return (
        len(value.get("bull_specialists", [])) >= 2
        and len(value.get("bear_specialists", [])) >= 2
        and value.get("single_sided_evidence") is False
    )


def _safety_valid(safety: Mapping[str, Any]) -> bool:
    return (
        safety.get("candidate_language_only") is True
        and safety.get("deterministic_evidence_validation") is True
        and safety.get("changes_synthesis") is False
        and safety.get("changes_risk_controls") is False
        and safety.get("real_trading_allowed") is False
    )


def _percent(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _baseline_metrics(
    raw: list[dict[str, Any]], signatures: set[str]
) -> dict[str, Any]:
    count = len(raw)
    return {
        "evidence_validity_rate_percent": _percent(
            sum(item["baseline"]["final_evidence_valid"] for item in raw), count
        ),
        "viewpoint_diversity_rate_percent": _percent(len(signatures), count),
        "bull_bear_balance_rate_percent": _percent(
            sum(item["baseline"]["bull_bear_balanced"] for item in raw), count
        ),
        "retry_rate_percent": 0.0,
        "fallback_rate_percent": 0.0,
        "average_latency_ms": 0.0,
        "total_tokens": 0,
        "result_stability_rate_percent": 100.0,
    }


def _dynamic_metrics(
    raw: list[dict[str, Any]], signatures: set[str]
) -> dict[str, Any]:
    count = len(raw)
    attempts = sum(item["dynamic"]["semantic_attempts"] for item in raw)
    rejected = sum(item["dynamic"]["rejected_candidates"] for item in raw)
    return {
        "candidate_evidence_validity_rate_percent": _percent(attempts - rejected, attempts),
        "final_evidence_validity_rate_percent": _percent(
            sum(item["dynamic"]["final_evidence_valid"] for item in raw), count
        ),
        "viewpoint_diversity_rate_percent": _percent(len(signatures), count),
        "bull_bear_balance_rate_percent": _percent(
            sum(item["dynamic"]["bull_bear_balanced"] for item in raw), count
        ),
        "retry_rate_percent": _percent(
            sum(item["dynamic"]["semantic_attempts"] > 1 for item in raw), count
        ),
        "fallback_rate_percent": _percent(
            sum(item["dynamic"]["mode"] != "dynamic" for item in raw), count
        ),
        "average_latency_ms": round(
            sum(item["dynamic"]["latency_ms"] for item in raw) / count, 2
        ),
        "total_tokens": sum(item["dynamic"]["tokens"] for item in raw),
        "result_stability_rate_percent": _stability_rate(raw),
    }


def _stability_rate(raw: list[dict[str, Any]]) -> float:
    by_case: dict[str, list[tuple[bool, bool, bool]]] = {}
    for item in raw:
        dynamic = item["dynamic"]
        by_case.setdefault(item["case_id"], []).append(
            (
                dynamic["final_evidence_valid"],
                dynamic["bull_bear_balanced"],
                dynamic["safety_valid"],
            )
        )
    stable = sum(
        len(set(values)) == 1
        for values in by_case.values()
        for _ in values
    )
    return _percent(stable, len(raw))


def print_dynamic_debate_evaluation(
    report: DynamicDebateEvaluationReport | Mapping[str, Any],
) -> None:
    """Print the complete evaluation in a stable Chinese terminal format."""

    value = report.to_mapping() if isinstance(report, DynamicDebateEvaluationReport) else report
    mode = "真实 DeepSeek" if value["live"] else "脚本化 Mock"
    print("=== 动态多空辩论固定评测 ===")
    print(f"模式: {mode}")
    print(f"评测集: {value['dataset']['name']}")
    print(
        f"用例={value['dataset']['case_count']}，重复运行={value['dataset']['run_count']}，"
        f"provider={value['provider']}，model={value['model']}"
    )
    baseline = value["baseline"]
    print("\n【固定模板基线】")
    print(f"- 证据有效率: {baseline['evidence_validity_rate_percent']:.2f}%")
    print(f"- 观点多样性: {baseline['viewpoint_diversity_rate_percent']:.2f}%")
    print(f"- 正反平衡率: {baseline['bull_bear_balance_rate_percent']:.2f}%")
    print(f"- 平均耗时: {baseline['average_latency_ms']:.2f} ms")
    print(f"- Token: {baseline['total_tokens']}")
    dynamic = value["dynamic"]
    print("\n【动态辩论】")
    print(f"- 候选证据有效率: {dynamic['candidate_evidence_validity_rate_percent']:.2f}%")
    print(f"- 最终证据有效率: {dynamic['final_evidence_validity_rate_percent']:.2f}%")
    print(f"- 观点多样性: {dynamic['viewpoint_diversity_rate_percent']:.2f}%")
    print(f"- 正反平衡率: {dynamic['bull_bear_balance_rate_percent']:.2f}%")
    print(f"- 重试率: {dynamic['retry_rate_percent']:.2f}%")
    print(f"- 降级率: {dynamic['fallback_rate_percent']:.2f}%")
    print(f"- 平均耗时: {dynamic['average_latency_ms']:.2f} ms")
    print(f"- Token: {dynamic['total_tokens']}")
    print(f"- 结果稳定性: {dynamic['result_stability_rate_percent']:.2f}%")
    print("\n【验收阈值】")
    for name, passed in value["acceptance"].items():
        print(f"- {'通过' if passed else '失败'}: {name}")
    print("\n【逐次原始结果】")
    for item in value["raw_results"]:
        current = item["dynamic"]
        print(
            f"- {item['case_id']}#{item['repetition']}: rounds={item['rounds']}，"
            f"mode={current['mode']}，attempts={current['semantic_attempts']}，"
            f"valid={current['final_evidence_valid']}，balanced={current['bull_bear_balanced']}，"
            f"tokens={current['tokens']}，latency_ms={current['latency_ms']}"
        )
    print("\n【结论边界】")
    if value["live"]:
        print("本结果来自本次真实 DeepSeek 调用，只代表固定评测集与当前模型配置。")
    else:
        print("脚本化 Mock 只验证评测链路和统计方法，不冒充真实 DeepSeek 质量。")
    print("LLM 只改写候选论证语言；证据、综合结论、仓位、风控和真实交易边界不变。")
    print("结论: " + ("补强评测通过" if value["passed"] else "补强评测失败"))


__all__ = [
    "DynamicDebateEvaluationReport",
    "DynamicDebateEvaluationRuntime",
    "print_dynamic_debate_evaluation",
]
