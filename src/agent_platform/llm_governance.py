"""Versioned, budgeted and cache-aware governance around model calls.

The deterministic financial pipeline stays outside this module.  This module
only governs language-model work: it records which prompt/schema policy was
used, limits provider calls and tokens, reuses successful in-memory results,
and exposes safe metadata for the customer UI and audit trail.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

from .core.model_gateway import ModelGatewayResult, ModelRequest


class LLMGovernanceError(RuntimeError):
    """A model call was rejected by the local governance policy."""


class GovernanceBudgetExceeded(LLMGovernanceError):
    """The request would exceed the configured call or token budget."""


@dataclass(frozen=True)
class GovernancePolicy:
    """Stable policy metadata and finite limits for one model route."""

    policy_version: str = "p7-policy-v1"
    prompt_version: str = "client-explanation-prompt-v1"
    schema_version: str = "client-explanation-schema-v1"
    route: str = "deepseek"
    max_calls: int = 4
    max_total_tokens: int = 2400
    max_output_tokens: int = 420
    cache_ttl_seconds: float = 300.0

    def __post_init__(self) -> None:
        for field_name in (
            "policy_version",
            "prompt_version",
            "schema_version",
            "route",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        for field_name in ("max_calls", "max_total_tokens", "max_output_tokens"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if (
            isinstance(self.cache_ttl_seconds, bool)
            or not isinstance(self.cache_ttl_seconds, (int, float))
            or not math.isfinite(float(self.cache_ttl_seconds))
            or self.cache_ttl_seconds < 0
        ):
            raise ValueError("cache_ttl_seconds must be a finite non-negative number")


@dataclass(frozen=True)
class GovernedModelResult:
    """A normal Gateway result plus the governance decision visible to callers."""

    response: Any
    trace: tuple[Any, ...]
    governance: Mapping[str, Any]


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    result: ModelGatewayResult


class ModelGovernanceRuntime:
    """Deep interface for versioning, budgets, caching and safe model metadata."""

    def __init__(
        self,
        gateway: Any,
        *,
        policy: GovernancePolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not hasattr(gateway, "generate"):
            raise TypeError("gateway must expose generate(request)")
        self._gateway = gateway
        self.policy = policy or GovernancePolicy()
        self._clock = clock
        self._lock = RLock()
        self._cache: dict[str, _CacheEntry] = {}
        self._calls_used = 0
        self._tokens_used = 0

    def generate(
        self,
        request: ModelRequest,
        *,
        operation: str = "model_call",
    ) -> GovernedModelResult:
        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        if not isinstance(operation, str) or not operation.strip():
            raise ValueError("operation must be a non-empty string")
        if request.max_output_tokens > self.policy.max_output_tokens:
            raise GovernanceBudgetExceeded(
                f"模型输出上限 {request.max_output_tokens} 超过治理上限 "
                f"{self.policy.max_output_tokens}。"
            )

        cache_key = self._cache_key(request, operation)
        now = self._clock()
        with self._lock:
            entry = self._cache.get(cache_key)
            if entry is not None:
                if self.policy.cache_ttl_seconds == 0 or entry.expires_at <= now:
                    self._cache.pop(cache_key, None)
                else:
                    return self._wrap(entry.result, operation=operation, cache_hit=True)
            self._check_budget_locked(request)
            self._calls_used += 1

        try:
            result = self._gateway.generate(request)
        except Exception:
            with self._lock:
                self._calls_used = max(0, self._calls_used - 1)
            raise

        usage = result.response.usage
        actual_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        with self._lock:
            self._tokens_used += actual_tokens
            if self._tokens_used > self.policy.max_total_tokens:
                raise GovernanceBudgetExceeded(
                    "模型实际 Token 消耗超过治理预算，已阻止结果进入缓存。"
                )
            if self.policy.cache_ttl_seconds > 0:
                self._cache[cache_key] = _CacheEntry(
                    expires_at=now + float(self.policy.cache_ttl_seconds),
                    result=result,
                )
        return self._wrap(result, operation=operation, cache_hit=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "policy_version": self.policy.policy_version,
                "prompt_version": self.policy.prompt_version,
                "schema_version": self.policy.schema_version,
                "route": self.policy.route,
                "max_calls": self.policy.max_calls,
                "max_total_tokens": self.policy.max_total_tokens,
                "max_output_tokens": self.policy.max_output_tokens,
                "cache_ttl_seconds": self.policy.cache_ttl_seconds,
                "calls_used": self._calls_used,
                "calls_remaining": max(0, self.policy.max_calls - self._calls_used),
                "tokens_used": self._tokens_used,
                "tokens_remaining": max(
                    0, self.policy.max_total_tokens - self._tokens_used
                ),
                "cache_entries": len(self._cache),
            }

    def reset_budget(self) -> None:
        """Reset the in-memory scope; cached successful results remain reusable."""

        with self._lock:
            self._calls_used = 0
            self._tokens_used = 0

    def _check_budget_locked(self, request: ModelRequest) -> None:
        if self._calls_used >= self.policy.max_calls:
            raise GovernanceBudgetExceeded("模型调用次数已达到本次治理预算。")
        estimated_input = max(
            1,
            math.ceil(
                (
                    len(request.prompt)
                    + len(request.system_prompt or "")
                )
                / 4
            ),
        )
        estimated_total = estimated_input + request.max_output_tokens
        if self._tokens_used + estimated_total > self.policy.max_total_tokens:
            raise GovernanceBudgetExceeded("本次模型调用预计会超过 Token 预算。")

    def _cache_key(self, request: ModelRequest, operation: str) -> str:
        payload = {
            "operation": operation,
            "policy_version": self.policy.policy_version,
            "prompt_version": self.policy.prompt_version,
            "schema_version": self.policy.schema_version,
            "route": self.policy.route,
            "prompt": request.prompt,
            "system_prompt": request.system_prompt,
            "response_schema": request.response_schema,
            "schema_name": request.schema_name,
            "max_output_tokens": request.max_output_tokens,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _wrap(
        self,
        result: ModelGatewayResult,
        *,
        operation: str,
        cache_hit: bool,
    ) -> GovernedModelResult:
        with self._lock:
            calls_used = self._calls_used
            tokens_used = self._tokens_used
        metadata = {
            "policy_version": self.policy.policy_version,
            "prompt_version": self.policy.prompt_version,
            "schema_version": self.policy.schema_version,
            "route": self.policy.route,
            "operation": operation,
            "cache_hit": cache_hit,
            "degraded": False,
            "fallback_reason": None,
            "calls_used": calls_used,
            "calls_remaining": max(0, self.policy.max_calls - calls_used),
            "tokens_used": tokens_used,
            "tokens_remaining": max(
                0, self.policy.max_total_tokens - tokens_used
            ),
        }
        return GovernedModelResult(
            response=result.response,
            trace=result.trace,
            governance=copy.deepcopy(metadata),
        )


def local_fallback_metadata(*, reason: str) -> dict[str, Any]:
    """Create safe metadata for deterministic local explanations."""

    return {
        "policy_version": "p7-policy-v1",
        "prompt_version": "local-rule-v1",
        "schema_version": "local-explanation-v1",
        "route": "local",
        "max_calls": 0,
        "max_total_tokens": 0,
        "max_output_tokens": 0,
        "cache_ttl_seconds": 0,
        "operation": "client_explanation",
        "cache_hit": False,
        "degraded": True,
        "fallback_reason": reason,
        "calls_used": 0,
        "calls_remaining": 0,
        "tokens_used": 0,
        "tokens_remaining": 0,
    }


__all__ = [
    "GovernanceBudgetExceeded",
    "GovernancePolicy",
    "GovernedModelResult",
    "LLMGovernanceError",
    "ModelGovernanceRuntime",
    "local_fallback_metadata",
]
