"""Unified model execution contracts, retry policy, and deterministic mock adapter."""

from __future__ import annotations

import copy
import json
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .contracts import AgentResponse, GuardrailConfigurationError, GuardrailViolation
from .guardrails import JSONSchemaValidator


_SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ModelErrorCode(str, Enum):
    """Provider-independent model failure categories."""

    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    TIMEOUT = "timeout"
    INVALID_REQUEST = "invalid_request"
    SERVICE_UNAVAILABLE = "service_unavailable"
    TRANSPORT = "transport"
    INVALID_RESPONSE = "invalid_response"
    REFUSAL = "refusal"


@dataclass(frozen=True)
class ModelRequest:
    """One provider-neutral text generation request."""

    prompt: str
    system_prompt: str | None = None
    response_schema: Mapping[str, Any] | None = None
    schema_name: str = "agent_response"
    max_output_tokens: int = 512

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ModelGatewayConfigurationError(
                "model request prompt must be a non-empty string"
            )
        if self.system_prompt is not None and (
            not isinstance(self.system_prompt, str) or not self.system_prompt.strip()
        ):
            raise ModelGatewayConfigurationError(
                "system_prompt must be None or a non-empty string"
            )
        if isinstance(self.max_output_tokens, bool) or not isinstance(
            self.max_output_tokens, int
        ) or self.max_output_tokens <= 0:
            raise ModelGatewayConfigurationError(
                "max_output_tokens must be a positive integer"
            )
        if not isinstance(self.schema_name, str) or not _SCHEMA_NAME_PATTERN.fullmatch(
            self.schema_name
        ):
            raise ModelGatewayConfigurationError(
                "schema_name must contain 1-64 letters, digits, underscores, or hyphens"
            )
        if self.response_schema is not None:
            if not isinstance(self.response_schema, Mapping):
                raise ModelGatewayConfigurationError(
                    "response_schema must be a JSON schema object"
                )
            object.__setattr__(
                self,
                "response_schema",
                copy.deepcopy(dict(self.response_schema)),
            )


@dataclass(frozen=True)
class ModelUsage:
    """Token counters reported by a model provider."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        for field_name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ModelGatewayConfigurationError(
                    f"{field_name} must be a non-negative integer"
                )


@dataclass(frozen=True)
class ModelAdapterResponse:
    """Raw-but-normalized result returned by any model adapter."""

    content: str
    model: str
    usage: ModelUsage = field(default_factory=ModelUsage)
    structured_output: Any | None = None
    response_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content:
            raise ModelGatewayConfigurationError(
                "adapter response content must be a non-empty string"
            )
        if not isinstance(self.model, str) or not self.model.strip():
            raise ModelGatewayConfigurationError(
                "adapter response model must be a non-empty string"
            )
        if self.response_id is not None and not isinstance(self.response_id, str):
            raise ModelGatewayConfigurationError(
                "adapter response_id must be None or a string"
            )


class ModelAdapter(Protocol):
    """The only seam a provider implementation must satisfy."""

    provider: str
    model: str

    def invoke(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> ModelAdapterResponse:
        """Execute one provider call or raise ModelProviderError."""


@dataclass(frozen=True)
class ModelRetryPolicy:
    """Finite retry and per-attempt timeout settings."""

    max_attempts: int = 3
    timeout_seconds: float = 30.0
    initial_backoff_seconds: float = 0.25
    backoff_multiplier: float = 2.0

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(
            self.max_attempts, int
        ) or self.max_attempts <= 0:
            raise ModelGatewayConfigurationError(
                "max_attempts must be a positive integer"
            )
        for field_name in (
            "timeout_seconds",
            "initial_backoff_seconds",
            "backoff_multiplier",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ModelGatewayConfigurationError(
                    f"{field_name} must be a finite number"
                )
            if not math.isfinite(float(value)):
                raise ModelGatewayConfigurationError(
                    f"{field_name} must be a finite number"
                )
        if self.timeout_seconds <= 0:
            raise ModelGatewayConfigurationError(
                "timeout_seconds must be greater than zero"
            )
        if self.initial_backoff_seconds < 0:
            raise ModelGatewayConfigurationError(
                "initial_backoff_seconds must not be negative"
            )
        if self.backoff_multiplier < 1:
            raise ModelGatewayConfigurationError(
                "backoff_multiplier must be at least one"
            )


@dataclass(frozen=True)
class ModelTraceEvent:
    """One ordered, provider-neutral model lifecycle event."""

    event: str
    provider: str
    model: str
    attempt: int = 0
    detail: str = ""


@dataclass(frozen=True)
class ModelResponse:
    """Successful model output with usage and execution metadata."""

    content: str
    structured_output: Any | None
    provider: str
    model: str
    usage: ModelUsage
    latency_ms: int
    attempts: int
    status: str = "succeeded"
    response_id: str | None = None


@dataclass(frozen=True)
class ModelGatewayResult:
    """Successful response plus the complete ordered model trace."""

    response: ModelResponse
    trace: tuple[ModelTraceEvent, ...]


class ModelGatewayConfigurationError(ValueError):
    """Model Gateway cannot be constructed or called safely."""


class ModelProviderError(RuntimeError):
    """An adapter-level failure already classified into a stable category."""

    def __init__(
        self,
        message: str,
        *,
        code: ModelErrorCode,
        retriable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retriable = retriable
        self.status_code = status_code


class ModelGatewayExecutionError(RuntimeError):
    """Final normalized failure with attempts, trace, and original cause."""

    def __init__(
        self,
        message: str,
        *,
        code: ModelErrorCode,
        retriable: bool,
        attempts: int,
        trace: tuple[ModelTraceEvent, ...],
        cause: Exception,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retriable = retriable
        self.attempts = attempts
        self.trace = trace
        self.cause = cause


class ModelGateway:
    """Apply one retry, timeout, validation, and tracing policy to all adapters."""

    def __init__(
        self,
        adapter: ModelAdapter,
        *,
        retry_policy: ModelRetryPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not getattr(adapter, "provider", "") or not getattr(adapter, "model", ""):
            raise ModelGatewayConfigurationError(
                "model adapter must expose non-empty provider and model names"
            )
        self._adapter = adapter
        self._policy = retry_policy or ModelRetryPolicy()
        self._clock = clock
        self._sleeper = sleeper

    def generate(self, request: ModelRequest) -> ModelGatewayResult:
        validator = self._prepare_validator(request)
        trace: list[ModelTraceEvent] = [self._event("gateway.started")]
        started_at = self._clock()
        final_error: ModelProviderError | None = None

        for attempt in range(1, self._policy.max_attempts + 1):
            trace.append(self._event("model.attempt.started", attempt=attempt))
            try:
                adapter_response = self._adapter.invoke(
                    request,
                    timeout_seconds=float(self._policy.timeout_seconds),
                )
                self._validate_response(request, adapter_response, validator)
            except Exception as error:  # adapters are an external trust boundary
                final_error = self._normalize_error(error)
                trace.append(
                    self._event(
                        "model.attempt.failed",
                        attempt=attempt,
                        detail=f"{final_error.code.value}: {final_error}",
                    )
                )
                if final_error.retriable and attempt < self._policy.max_attempts:
                    delay = float(self._policy.initial_backoff_seconds) * (
                        float(self._policy.backoff_multiplier) ** (attempt - 1)
                    )
                    trace.append(
                        self._event(
                            "model.retry.scheduled",
                            attempt=attempt,
                            detail=f"delay_seconds={delay:g}",
                        )
                    )
                    self._sleeper(delay)
                    continue

                trace.append(
                    self._event(
                        "gateway.failed",
                        attempt=attempt,
                        detail=f"{final_error.code.value}: {final_error}",
                    )
                )
                raise ModelGatewayExecutionError(
                    f"model call failed: {final_error}",
                    code=final_error.code,
                    retriable=final_error.retriable,
                    attempts=attempt,
                    trace=tuple(trace),
                    cause=final_error,
                ) from error

            latency_ms = max(0, round((self._clock() - started_at) * 1000))
            trace.append(self._event("model.attempt.succeeded", attempt=attempt))
            trace.append(
                self._event(
                    "gateway.succeeded",
                    attempt=attempt,
                    detail=(
                        f"input_tokens={adapter_response.usage.input_tokens}; "
                        f"output_tokens={adapter_response.usage.output_tokens}; "
                        f"latency_ms={latency_ms}"
                    ),
                )
            )
            response = ModelResponse(
                content=adapter_response.content,
                structured_output=copy.deepcopy(adapter_response.structured_output),
                provider=self._adapter.provider,
                model=adapter_response.model,
                usage=adapter_response.usage,
                latency_ms=latency_ms,
                attempts=attempt,
                response_id=adapter_response.response_id,
            )
            return ModelGatewayResult(response=response, trace=tuple(trace))

        raise AssertionError(f"unreachable model gateway state: {final_error}")

    def _prepare_validator(
        self,
        request: ModelRequest,
    ) -> JSONSchemaValidator | None:
        if request.response_schema is None:
            return None
        try:
            return JSONSchemaValidator(
                output_schema=request.response_schema,
                output_path="metadata.model_output",
                name="model_structured_output",
            )
        except GuardrailConfigurationError as error:
            raise ModelGatewayConfigurationError(
                f"unsupported response_schema: {error}"
            ) from error

    @staticmethod
    def _validate_response(
        request: ModelRequest,
        response: ModelAdapterResponse,
        validator: JSONSchemaValidator | None,
    ) -> None:
        if request.response_schema is None:
            return
        if response.structured_output is None:
            raise ModelProviderError(
                "provider did not return structured output",
                code=ModelErrorCode.INVALID_RESPONSE,
                retriable=False,
            )
        try:
            json.dumps(response.structured_output, allow_nan=False)
            assert validator is not None
            validator.check_output(
                AgentResponse(
                    content=response.content,
                    metadata={"model_output": response.structured_output},
                )
            )
        except (TypeError, ValueError, GuardrailViolation) as error:
            raise ModelProviderError(
                f"structured output failed local validation: {error}",
                code=ModelErrorCode.INVALID_RESPONSE,
                retriable=False,
            ) from error

    @staticmethod
    def _normalize_error(error: Exception) -> ModelProviderError:
        if isinstance(error, ModelProviderError):
            return error
        if isinstance(error, TimeoutError):
            return ModelProviderError(
                "model adapter timed out",
                code=ModelErrorCode.TIMEOUT,
                retriable=True,
            )
        return ModelProviderError(
            f"unexpected adapter failure: {error}",
            code=ModelErrorCode.TRANSPORT,
            retriable=False,
        )

    def _event(
        self,
        event: str,
        *,
        attempt: int = 0,
        detail: str = "",
    ) -> ModelTraceEvent:
        return ModelTraceEvent(
            event=event,
            provider=self._adapter.provider,
            model=self._adapter.model,
            attempt=attempt,
            detail=detail,
        )


class MockModelAdapter:
    """Deterministic offline adapter with an optional scripted outcome sequence."""

    provider = "mock"

    def __init__(
        self,
        *,
        model: str = "mock-deterministic-v1",
        content: str | None = None,
        structured_output: Any | None = None,
        usage: ModelUsage | None = None,
        script: Iterable[ModelAdapterResponse | Exception] = (),
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ModelGatewayConfigurationError(
                "mock model must be a non-empty string"
            )
        self.model = model.strip()
        self._content = content
        self._structured_output = copy.deepcopy(structured_output)
        self._usage = usage or ModelUsage()
        self._script = list(script)
        self.calls = 0
        self.timeouts: list[float] = []

    def invoke(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> ModelAdapterResponse:
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        if self._script:
            outcome = self._script.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        content = self._content if self._content is not None else request.prompt
        structured_output = copy.deepcopy(self._structured_output)
        if request.response_schema is not None and structured_output is None:
            try:
                structured_output = json.loads(content)
            except json.JSONDecodeError as error:
                raise ModelProviderError(
                    "mock response is not valid structured JSON",
                    code=ModelErrorCode.INVALID_RESPONSE,
                    retriable=False,
                ) from error
        return ModelAdapterResponse(
            content=content,
            structured_output=structured_output,
            model=self.model,
            usage=self._usage,
            response_id=f"mock-{self.calls}",
        )
