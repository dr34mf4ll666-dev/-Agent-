"""Explicit quality gates and release rollback for model/prompt changes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class QualityGatePolicy:
    """Acceptance requirements for promoting a model configuration."""

    require_live: bool = False
    require_all_acceptance_checks: bool = True
    require_raw_results: bool = True

    def __post_init__(self) -> None:
        for name in (
            "require_live",
            "require_all_acceptance_checks",
            "require_raw_results",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True)
class ModelRelease:
    """The version tuple that can become the active model configuration."""

    prompt_version: str
    schema_version: str
    model: str
    provider: str = "deepseek"

    def __post_init__(self) -> None:
        for name in ("prompt_version", "schema_version", "model", "provider"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")

    def to_mapping(self) -> dict[str, str]:
        return asdict(self)


class LLMQualityGateRuntime:
    """Evaluate fixed evaluation output without recomputing model metrics."""

    def evaluate(
        self,
        report: Mapping[str, Any],
        *,
        policy: QualityGatePolicy | None = None,
    ) -> dict[str, Any]:
        if not isinstance(report, Mapping):
            raise ValueError("quality gate report must be an object")
        policy = policy or QualityGatePolicy()
        acceptance = report.get("acceptance", {})
        raw_results = report.get("raw_results", [])
        safety_valid = (
            isinstance(raw_results, list)
            and bool(raw_results)
            and all(
                isinstance(item, Mapping)
                and isinstance(item.get("dynamic"), Mapping)
                and item["dynamic"].get("safety_valid") is True
                for item in raw_results
            )
        )
        checks = {
            "evaluation_passed": report.get("passed") is True,
            "real_model_run": report.get("live") is True,
            "raw_results_retained": bool(raw_results),
            "safety_boundary": safety_valid,
            "acceptance_checks": (
                isinstance(acceptance, Mapping)
                and bool(acceptance)
                and all(value is True for value in acceptance.values())
            ),
            "provider_declared": bool(str(report.get("provider", "")).strip()),
            "model_declared": bool(str(report.get("model", "")).strip()),
        }
        if not policy.require_live:
            checks["real_model_run"] = True
        if not policy.require_raw_results:
            checks["raw_results_retained"] = True
        if not policy.require_all_acceptance_checks:
            checks["acceptance_checks"] = True
        passed = all(checks.values())
        return {
            "passed": passed,
            "can_promote": passed,
            "policy": asdict(policy),
            "checks": checks,
            "provider": report.get("provider", "unknown"),
            "model": report.get("model", "unknown"),
            "live": bool(report.get("live", False)),
            "raw_result_count": len(raw_results) if isinstance(raw_results, list) else 0,
            "conclusion": (
                "满足当前发布门禁，可以进入候选版本。"
                if passed
                else "未满足当前发布门禁，继续使用原版本并保留现有降级路径。"
            ),
        }


class ModelReleaseRegistry:
    """Small in-memory release seam with explicit promotion and rollback."""

    def __init__(self, active: ModelRelease) -> None:
        if not isinstance(active, ModelRelease):
            raise TypeError("active must be a ModelRelease")
        self._active = active
        self._previous: ModelRelease | None = None

    @property
    def active(self) -> ModelRelease:
        return self._active

    def promote(
        self,
        candidate: ModelRelease,
        gate_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(candidate, ModelRelease):
            raise TypeError("candidate must be a ModelRelease")
        if not isinstance(gate_result, Mapping) or gate_result.get("can_promote") is not True:
            return {
                "promoted": False,
                "active": self._active.to_mapping(),
                "reason": "质量门禁未通过，候选版本没有成为默认版本。",
            }
        self._previous = self._active
        self._active = candidate
        return {"promoted": True, "active": candidate.to_mapping()}

    def rollback(self) -> dict[str, Any]:
        if self._previous is None:
            return {
                "rolled_back": False,
                "active": self._active.to_mapping(),
                "reason": "没有可回滚的上一版本。",
            }
        previous = self._previous
        self._previous = self._active
        self._active = previous
        return {"rolled_back": True, "active": self._active.to_mapping()}


__all__ = [
    "LLMQualityGateRuntime",
    "ModelRelease",
    "ModelReleaseRegistry",
    "QualityGatePolicy",
]
