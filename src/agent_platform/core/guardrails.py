"""Built-in Guardrails and their configuration registry."""

from __future__ import annotations

import copy
import math
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from threading import Lock
from typing import Any

from .contracts import (
    AgentRequest,
    AgentResponse,
    Guardrail,
    GuardrailConfigurationError,
    GuardrailViolation,
)


_SUPPORTED_SCHEMA_TYPES = {
    "object",
    "array",
    "string",
    "number",
    "integer",
    "boolean",
    "null",
}
_SUPPORTED_SCHEMA_KEYS = {
    "$schema",
    "$id",
    "title",
    "description",
    "default",
    "examples",
    "type",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
}


class _SchemaMismatch(ValueError):
    pass


def _validate_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise GuardrailConfigurationError(
            "guardrail name must be a non-empty string"
        )
    return name.strip()


def _normalize_paths(
    paths: str | Sequence[str],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if isinstance(paths, str):
        normalized = (paths,)
    elif isinstance(paths, Sequence):
        normalized = tuple(paths)
    else:
        raise GuardrailConfigurationError(
            f"{field_name} must be a path string or a sequence of path strings"
        )
    for path in normalized:
        if not isinstance(path, str) or not path.strip():
            raise GuardrailConfigurationError(
                f"{field_name} must contain only non-empty path strings"
            )
    return tuple(path.strip() for path in normalized)


def _request_payload(request: AgentRequest) -> dict[str, Any]:
    return {"task": request.task, "context": request.context}


def _response_payload(response: AgentResponse) -> dict[str, Any]:
    return {"content": response.content, "metadata": response.metadata}


def _resolve_path(root: Any, path: str) -> Any:
    if path in {"$", "."}:
        return root
    normalized = path[2:] if path.startswith("$.") else path
    current = root
    for part in normalized.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                raise GuardrailViolation(f"payload path not found: {path}")
            current = current[part]
            continue
        if isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise GuardrailViolation(f"payload path not found: {path}")
            current = current[index]
            continue
        raise GuardrailViolation(f"payload path not found: {path}")
    return current


def _schema_types(schema_type: Any, *, path: str) -> tuple[str, ...]:
    if isinstance(schema_type, str):
        types = (schema_type,)
    elif isinstance(schema_type, Sequence) and not isinstance(
        schema_type, (str, bytes)
    ):
        types = tuple(schema_type)
    else:
        raise GuardrailConfigurationError(
            f"schema {path}.type must be a string or sequence of strings"
        )
    if not types or any(
        not isinstance(item, str) or item not in _SUPPORTED_SCHEMA_TYPES
        for item in types
    ):
        raise GuardrailConfigurationError(
            f"schema {path}.type contains an unsupported JSON type"
        )
    return types


def _validate_non_negative_integer(value: Any, *, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GuardrailConfigurationError(
            f"schema {path} must be a non-negative integer"
        )


def _validate_schema_definition(schema: Mapping[str, Any], *, path: str) -> None:
    unsupported = set(schema) - _SUPPORTED_SCHEMA_KEYS
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise GuardrailConfigurationError(
            f"schema {path} uses unsupported keywords: {names}"
        )

    if "type" in schema:
        _schema_types(schema["type"], path=path)

    required = schema.get("required")
    if required is not None:
        if (
            not isinstance(required, Sequence)
            or isinstance(required, (str, bytes))
            or any(not isinstance(item, str) or not item for item in required)
        ):
            raise GuardrailConfigurationError(
                f"schema {path}.required must be a sequence of field names"
            )

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise GuardrailConfigurationError(
                f"schema {path}.properties must be an object"
            )
        for key, child_schema in properties.items():
            if not isinstance(key, str) or not isinstance(child_schema, Mapping):
                raise GuardrailConfigurationError(
                    f"schema {path}.properties must map names to schemas"
                )
            _validate_schema_definition(
                child_schema,
                path=f"{path}.properties.{key}",
            )

    additional_properties = schema.get("additionalProperties")
    if additional_properties is not None and not isinstance(
        additional_properties, bool
    ):
        raise GuardrailConfigurationError(
            f"schema {path}.additionalProperties must be a boolean"
        )

    items = schema.get("items")
    if items is not None:
        if not isinstance(items, Mapping):
            raise GuardrailConfigurationError(
                f"schema {path}.items must be a schema object"
            )
        _validate_schema_definition(items, path=f"{path}.items")

    enum = schema.get("enum")
    if enum is not None and (
        not isinstance(enum, Sequence)
        or isinstance(enum, (str, bytes))
        or not enum
    ):
        raise GuardrailConfigurationError(
            f"schema {path}.enum must be a non-empty sequence"
        )

    for keyword in ("minimum", "maximum"):
        value = schema.get(keyword)
        if value is not None and not _is_json_number(value):
            raise GuardrailConfigurationError(
                f"schema {path}.{keyword} must be a finite number"
            )
    if (
        schema.get("minimum") is not None
        and schema.get("maximum") is not None
        and schema["minimum"] > schema["maximum"]
    ):
        raise GuardrailConfigurationError(
            f"schema {path}.minimum must not exceed maximum"
        )

    for keyword in ("minLength", "maxLength", "minItems", "maxItems"):
        if keyword in schema:
            _validate_non_negative_integer(
                schema[keyword],
                path=f"{path}.{keyword}",
            )
    for minimum_name, maximum_name in (
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
    ):
        if (
            minimum_name in schema
            and maximum_name in schema
            and schema[minimum_name] > schema[maximum_name]
        ):
            raise GuardrailConfigurationError(
                f"schema {path}.{minimum_name} must not exceed {maximum_name}"
            )


def _is_json_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Decimal):
        return value.is_finite()
    return True


def _matches_json_type(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, Mapping)
    if schema_type == "array":
        return isinstance(value, (list, tuple))
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return _is_json_number(value)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return False


def _child_path(path: str, part: str | int) -> str:
    if isinstance(part, int):
        return f"{path}[{part}]"
    return f"{path}.{part}"


def _validate_json_value(
    value: Any,
    schema: Mapping[str, Any],
    *,
    path: str,
) -> None:
    if "enum" in schema and value not in schema["enum"]:
        raise _SchemaMismatch(f"{path} must be one of {list(schema['enum'])!r}")
    if "const" in schema and value != schema["const"]:
        raise _SchemaMismatch(f"{path} must equal {schema['const']!r}")

    if "type" in schema:
        schema_types = _schema_types(schema["type"], path=path)
        if not any(_matches_json_type(value, item) for item in schema_types):
            expected = " or ".join(schema_types)
            raise _SchemaMismatch(f"{path} must be of type {expected}")

    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _SchemaMismatch(f"{path} object keys must be strings")
        for field in schema.get("required", ()):
            if field not in value:
                raise _SchemaMismatch(
                    f"{_child_path(path, field)} is a required field"
                )
        properties = schema.get("properties", {})
        for field, child_schema in properties.items():
            if field in value:
                _validate_json_value(
                    value[field],
                    child_schema,
                    path=_child_path(path, field),
                )
        if schema.get("additionalProperties") is False:
            unexpected = sorted(set(value) - set(properties))
            if unexpected:
                raise _SchemaMismatch(
                    f"{path} contains unexpected fields: {', '.join(unexpected)}"
                )

    if isinstance(value, (list, tuple)):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise _SchemaMismatch(f"{path} must contain at least {minimum} items")
        if maximum is not None and len(value) > maximum:
            raise _SchemaMismatch(f"{path} must contain at most {maximum} items")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate_json_value(
                    item,
                    schema["items"],
                    path=_child_path(path, index),
                )

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            raise _SchemaMismatch(
                f"{path} must contain at least {minimum} characters"
            )
        if maximum is not None and len(value) > maximum:
            raise _SchemaMismatch(
                f"{path} must contain at most {maximum} characters"
            )

    if _is_json_number(value):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            raise _SchemaMismatch(f"{path} must be at least {minimum}")
        if maximum is not None and value > maximum:
            raise _SchemaMismatch(f"{path} must be at most {maximum}")


class JSONSchemaValidator:
    """Validate selected request or response payloads against a safe subset."""

    def __init__(
        self,
        *,
        input_schema: Mapping[str, Any] | None = None,
        output_schema: Mapping[str, Any] | None = None,
        input_path: str = "context",
        output_path: str = "metadata",
        name: str = "json_schema",
    ) -> None:
        self.name = _validate_name(name)
        if input_schema is None and output_schema is None:
            raise GuardrailConfigurationError(
                "JSONSchemaValidator requires input_schema or output_schema"
            )
        self._input_path = _normalize_paths(
            input_path,
            field_name="input_path",
        )[0]
        self._output_path = _normalize_paths(
            output_path,
            field_name="output_path",
        )[0]
        self._input_schema = self._prepare_schema(input_schema, stage="input")
        self._output_schema = self._prepare_schema(output_schema, stage="output")

    @staticmethod
    def _prepare_schema(
        schema: Mapping[str, Any] | None,
        *,
        stage: str,
    ) -> Mapping[str, Any] | None:
        if schema is None:
            return None
        if not isinstance(schema, Mapping):
            raise GuardrailConfigurationError(
                f"{stage}_schema must be a schema object"
            )
        copied = copy.deepcopy(dict(schema))
        _validate_schema_definition(copied, path="$schema")
        return copied

    def check_input(self, request: AgentRequest) -> None:
        if self._input_schema is not None:
            self._validate(
                _request_payload(request),
                self._input_path,
                self._input_schema,
                stage="input",
            )

    def check_output(self, response: AgentResponse) -> None:
        if self._output_schema is not None:
            self._validate(
                _response_payload(response),
                self._output_path,
                self._output_schema,
                stage="output",
            )

    def _validate(
        self,
        root: Mapping[str, Any],
        payload_path: str,
        schema: Mapping[str, Any],
        *,
        stage: str,
    ) -> None:
        selected = _resolve_path(root, payload_path)
        try:
            display_path = "$" if payload_path in {"$", "."} else f"$.{payload_path}"
            _validate_json_value(selected, schema, path=display_path)
        except _SchemaMismatch as error:
            raise GuardrailViolation(
                f"{self.name} rejected {stage} payload: {error}"
            ) from error


class SourceAttributionFilter:
    """Require provenance fields on explicitly selected records."""

    def __init__(
        self,
        *,
        required_fields: Sequence[str] = ("source", "timestamp"),
        input_paths: str | Sequence[str] = (),
        output_paths: str | Sequence[str] = ("metadata",),
        name: str = "source_attribution",
    ) -> None:
        self.name = _validate_name(name)
        if (
            not isinstance(required_fields, Sequence)
            or isinstance(required_fields, (str, bytes))
            or not required_fields
            or any(not isinstance(item, str) or not item for item in required_fields)
        ):
            raise GuardrailConfigurationError(
                "required_fields must be a non-empty sequence of field names"
            )
        self._required_fields = tuple(required_fields)
        self._input_paths = _normalize_paths(
            input_paths,
            field_name="input_paths",
        )
        self._output_paths = _normalize_paths(
            output_paths,
            field_name="output_paths",
        )
        if not self._input_paths and not self._output_paths:
            raise GuardrailConfigurationError(
                "SourceAttributionFilter requires an input or output path"
            )

    def check_input(self, request: AgentRequest) -> None:
        self._check_paths(_request_payload(request), self._input_paths, stage="input")

    def check_output(self, response: AgentResponse) -> None:
        self._check_paths(
            _response_payload(response),
            self._output_paths,
            stage="output",
        )

    def _check_paths(
        self,
        root: Mapping[str, Any],
        paths: tuple[str, ...],
        *,
        stage: str,
    ) -> None:
        for path in paths:
            selected = _resolve_path(root, path)
            if isinstance(selected, Mapping):
                records: Iterable[Any] = (selected,)
            elif isinstance(selected, (list, tuple)):
                records = selected
            else:
                raise GuardrailViolation(
                    f"{self.name} expected an object or record list at {path}"
                )
            for index, record in enumerate(records):
                record_path = path if isinstance(selected, Mapping) else f"{path}[{index}]"
                if not isinstance(record, Mapping):
                    raise GuardrailViolation(
                        f"{self.name} expected an object at {record_path}"
                    )
                missing = [
                    field
                    for field in self._required_fields
                    if field not in record
                    or record[field] is None
                    or (isinstance(record[field], str) and not record[field].strip())
                ]
                if missing:
                    raise GuardrailViolation(
                        f"{self.name} rejected {stage} record at {record_path}; "
                        f"missing provenance: {', '.join(missing)}"
                    )


class RateLimiter:
    """Process-local sliding-window rate limiter for one Harness instance."""

    def __init__(
        self,
        *,
        max_calls: int,
        period_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        name: str = "rate_limiter",
    ) -> None:
        self.name = _validate_name(name)
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls <= 0:
            raise GuardrailConfigurationError("max_calls must be a positive integer")
        if (
            isinstance(period_seconds, bool)
            or not isinstance(period_seconds, (int, float))
            or not math.isfinite(float(period_seconds))
            or period_seconds <= 0
        ):
            raise GuardrailConfigurationError(
                "period_seconds must be a finite positive number"
            )
        if not callable(clock):
            raise GuardrailConfigurationError("clock must be callable")
        self._max_calls = max_calls
        self._period_seconds = float(period_seconds)
        self._clock = clock
        self._calls: deque[float] = deque()
        self._last_seen: float | None = None
        self._lock = Lock()

    def check_input(self, request: AgentRequest) -> None:
        del request
        now = float(self._clock())
        if not math.isfinite(now):
            raise GuardrailViolation(f"{self.name} clock returned a non-finite value")
        with self._lock:
            if self._last_seen is not None and now < self._last_seen:
                raise GuardrailViolation(f"{self.name} clock moved backwards")
            self._last_seen = now
            cutoff = now - self._period_seconds
            while self._calls and self._calls[0] <= cutoff:
                self._calls.popleft()
            if len(self._calls) >= self._max_calls:
                raise GuardrailViolation(
                    f"{self.name} exceeded {self._max_calls} calls per "
                    f"{self._period_seconds:g} seconds"
                )
            self._calls.append(now)

    def check_output(self, response: AgentResponse) -> None:
        del response


def _iter_text_values(value: Any, *, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _iter_text_values(child, path=_child_path(path, str(key)))
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _iter_text_values(child, path=_child_path(path, index))


class KeywordBlocker:
    """Block configured text fragments in selected input and output payloads."""

    def __init__(
        self,
        blocked_keywords: Sequence[str],
        *,
        input_paths: str | Sequence[str] = ("task",),
        output_paths: str | Sequence[str] = ("content",),
        case_sensitive: bool = False,
        name: str = "keyword_blocker",
    ) -> None:
        self.name = _validate_name(name)
        if (
            not isinstance(blocked_keywords, Sequence)
            or isinstance(blocked_keywords, (str, bytes))
            or not blocked_keywords
            or any(not isinstance(item, str) or not item for item in blocked_keywords)
        ):
            raise GuardrailConfigurationError(
                "blocked_keywords must be a non-empty sequence of strings"
            )
        if not isinstance(case_sensitive, bool):
            raise GuardrailConfigurationError("case_sensitive must be a boolean")
        self._blocked_keywords = tuple(blocked_keywords)
        self._input_paths = _normalize_paths(
            input_paths,
            field_name="input_paths",
        )
        self._output_paths = _normalize_paths(
            output_paths,
            field_name="output_paths",
        )
        if not self._input_paths and not self._output_paths:
            raise GuardrailConfigurationError(
                "KeywordBlocker requires an input or output path"
            )
        self._case_sensitive = case_sensitive

    def check_input(self, request: AgentRequest) -> None:
        self._check_paths(_request_payload(request), self._input_paths, stage="input")

    def check_output(self, response: AgentResponse) -> None:
        self._check_paths(
            _response_payload(response),
            self._output_paths,
            stage="output",
        )

    def _check_paths(
        self,
        root: Mapping[str, Any],
        paths: tuple[str, ...],
        *,
        stage: str,
    ) -> None:
        for path in paths:
            selected = _resolve_path(root, path)
            for text_path, text in _iter_text_values(selected, path=path):
                candidate = text if self._case_sensitive else text.casefold()
                for keyword in self._blocked_keywords:
                    expected = keyword if self._case_sensitive else keyword.casefold()
                    if expected in candidate:
                        raise GuardrailViolation(
                            f"{self.name} rejected {stage} text at {text_path}; "
                            f"blocked keyword: {keyword}"
                        )


@dataclass(frozen=True)
class CrossValidationResult:
    """Result returned by deterministic cross-validation code."""

    valid: bool
    detail: str = ""


CrossValidationCheck = Callable[[Any], bool | CrossValidationResult]


class CrossValidator:
    """Validate selected output with independently injected deterministic code."""

    def __init__(
        self,
        validator: CrossValidationCheck,
        *,
        output_path: str = "metadata",
        name: str = "cross_validator",
    ) -> None:
        self.name = _validate_name(name)
        if not callable(validator):
            raise GuardrailConfigurationError("validator must be callable")
        self._validator = validator
        self._output_path = _normalize_paths(
            output_path,
            field_name="output_path",
        )[0]

    def check_input(self, request: AgentRequest) -> None:
        del request

    def check_output(self, response: AgentResponse) -> None:
        selected = _resolve_path(_response_payload(response), self._output_path)
        try:
            result = self._validator(selected)
        except Exception as error:
            raise GuardrailViolation(
                f"{self.name} deterministic validator failed: {error}"
            ) from error
        if isinstance(result, bool):
            validation = CrossValidationResult(valid=result)
        elif isinstance(result, CrossValidationResult):
            validation = result
        else:
            raise GuardrailViolation(
                f"{self.name} validator must return bool or CrossValidationResult"
            )
        if not validation.valid:
            detail = validation.detail or "deterministic result did not match"
            raise GuardrailViolation(f"{self.name} rejected output: {detail}")


GuardrailFactory = Callable[[Mapping[str, Any]], Guardrail]


class GuardrailRegistry:
    """Build built-in or custom Guardrail adapters from serializable configs."""

    def __init__(self) -> None:
        self._factories: dict[str, GuardrailFactory] = {}

    @classmethod
    def with_builtins(
        cls,
        *,
        cross_validators: Mapping[str, CrossValidationCheck] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> GuardrailRegistry:
        registry = cls()
        validators = dict(cross_validators or {})

        registry.register(
            "json_schema",
            lambda config: _construct_guardrail(JSONSchemaValidator, config),
        )
        registry.register(
            "source_attribution",
            lambda config: _construct_guardrail(SourceAttributionFilter, config),
        )

        def build_rate_limiter(config: Mapping[str, Any]) -> Guardrail:
            if "clock" in config:
                raise GuardrailConfigurationError(
                    "rate_limiter clock must be injected into the registry"
                )
            return _construct_guardrail(
                RateLimiter,
                {**config, "clock": clock},
            )

        registry.register("rate_limiter", build_rate_limiter)
        registry.register(
            "keyword_blocker",
            lambda config: _construct_guardrail(KeywordBlocker, config),
        )

        def build_cross_validator(config: Mapping[str, Any]) -> Guardrail:
            values = dict(config)
            validator_name = values.pop("validator", None)
            if not isinstance(validator_name, str) or not validator_name:
                raise GuardrailConfigurationError(
                    "cross_validator config requires validator name"
                )
            if validator_name not in validators:
                raise GuardrailConfigurationError(
                    f"cross validator is not registered: {validator_name}"
                )
            return _construct_guardrail(
                CrossValidator,
                {**values, "validator": validators[validator_name]},
            )

        registry.register("cross_validator", build_cross_validator)
        return registry

    def register(self, type_name: str, factory: GuardrailFactory) -> None:
        if not isinstance(type_name, str) or not type_name.strip():
            raise GuardrailConfigurationError(
                "guardrail type name must be a non-empty string"
            )
        normalized = type_name.strip()
        if normalized in self._factories:
            raise GuardrailConfigurationError(
                f"guardrail type is already registered: {normalized}"
            )
        if not callable(factory):
            raise GuardrailConfigurationError("guardrail factory must be callable")
        self._factories[normalized] = factory

    def build(self, configs: Iterable[Mapping[str, Any]]) -> tuple[Guardrail, ...]:
        guardrails: list[Guardrail] = []
        names: set[str] = set()
        for index, config in enumerate(configs):
            if not isinstance(config, Mapping):
                raise GuardrailConfigurationError(
                    f"guardrail config at index {index} must be an object"
                )
            values = dict(config)
            type_name = values.pop("type", None)
            if not isinstance(type_name, str) or not type_name:
                raise GuardrailConfigurationError(
                    f"guardrail config at index {index} requires type"
                )
            if type_name not in self._factories:
                raise GuardrailConfigurationError(
                    f"unknown guardrail type: {type_name}"
                )
            guardrail = self._factories[type_name](values)
            name = getattr(guardrail, "name", None)
            if not isinstance(name, str) or not name:
                raise GuardrailConfigurationError(
                    f"guardrail factory {type_name} returned an invalid adapter"
                )
            if name in names:
                raise GuardrailConfigurationError(
                    f"guardrail names must be unique; duplicate: {name}"
                )
            if not callable(getattr(guardrail, "check_input", None)) or not callable(
                getattr(guardrail, "check_output", None)
            ):
                raise GuardrailConfigurationError(
                    f"guardrail factory {type_name} returned an invalid adapter"
                )
            guardrails.append(guardrail)
            names.add(name)
        return tuple(guardrails)


def _construct_guardrail(
    guardrail_type: Callable[..., Guardrail],
    config: Mapping[str, Any],
) -> Guardrail:
    try:
        return guardrail_type(**dict(config))
    except GuardrailConfigurationError:
        raise
    except TypeError as error:
        raise GuardrailConfigurationError(
            f"invalid {guardrail_type.__name__} config: {error}"
        ) from error
