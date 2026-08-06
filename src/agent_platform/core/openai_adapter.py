"""OpenAI Responses API adapter implemented with the Python standard library."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from .model_http import JsonHttpTransport, post_json
from .model_gateway import (
    ModelAdapterResponse,
    ModelErrorCode,
    ModelGatewayConfigurationError,
    ModelProviderError,
    ModelRequest,
    ModelUsage,
)


OpenAITransport = JsonHttpTransport

_NON_RETRIABLE_QUOTA_CODES = {
    "credit_balance_exhausted",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
}


class OpenAIResponsesAdapter:
    """Translate the provider-neutral contract to OpenAI's Responses API."""

    provider = "openai"

    def __init__(
        self,
        *,
        model: str,
        env: Mapping[str, str] | None = None,
        base_url: str = "https://api.openai.com/v1",
        transport: OpenAITransport | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ModelGatewayConfigurationError(
                "OpenAI model must be a non-empty string"
            )
        environment = os.environ if env is None else env
        api_key = environment.get("OPENAI_API_KEY", "")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ModelGatewayConfigurationError(
                "OPENAI_API_KEY is required for the OpenAI adapter"
            )
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise ModelGatewayConfigurationError(
                "OpenAI base_url must be an HTTP(S) URL"
            )
        self.model = model.strip()
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._transport = transport or self._http_transport

    @classmethod
    def from_env(
        cls,
        *,
        model: str,
        env: Mapping[str, str] | None = None,
        base_url: str = "https://api.openai.com/v1",
        transport: OpenAITransport | None = None,
    ) -> OpenAIResponsesAdapter:
        environment = os.environ if env is None else env
        return cls(
            model=model,
            env=environment,
            base_url=base_url,
            transport=transport,
        )

    def invoke(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> ModelAdapterResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": self._build_input(request),
            "max_output_tokens": request.max_output_tokens,
            "store": False,
        }
        if request.response_schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "schema": request.response_schema,
                    "strict": True,
                }
            }

        response = self._transport(
            f"{self._base_url}/responses",
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload,
            timeout_seconds,
        )
        return self._parse_response(response, structured=request.response_schema is not None)

    @staticmethod
    def _build_input(request: ModelRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system_prompt is not None:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        return messages

    def _parse_response(
        self,
        response: Mapping[str, Any],
        *,
        structured: bool,
    ) -> ModelAdapterResponse:
        if not isinstance(response, Mapping):
            raise self._invalid_response("response body must be a JSON object")
        status = response.get("status")
        if status != "completed":
            reason = response.get("incomplete_details")
            raise self._invalid_response(
                f"response status is {status!r}; details={reason!r}"
            )

        content = self._extract_output_text(response)
        structured_output: Any | None = None
        if structured:
            try:
                structured_output = json.loads(content)
            except json.JSONDecodeError as error:
                raise self._invalid_response(
                    "structured response content is not valid JSON"
                ) from error

        usage_payload = response.get("usage")
        if not isinstance(usage_payload, Mapping):
            raise self._invalid_response("response usage must be a JSON object")
        try:
            usage = ModelUsage(
                input_tokens=usage_payload["input_tokens"],
                output_tokens=usage_payload["output_tokens"],
                total_tokens=usage_payload["total_tokens"],
            )
        except (KeyError, ModelGatewayConfigurationError) as error:
            raise self._invalid_response(
                "response usage is missing valid token counters"
            ) from error

        response_model = response.get("model")
        if not isinstance(response_model, str) or not response_model:
            raise self._invalid_response("response model must be a non-empty string")
        response_id = response.get("id")
        if response_id is not None and not isinstance(response_id, str):
            raise self._invalid_response("response id must be a string when present")
        return ModelAdapterResponse(
            content=content,
            structured_output=structured_output,
            model=response_model,
            usage=usage,
            response_id=response_id,
        )

    @staticmethod
    def _extract_output_text(response: Mapping[str, Any]) -> str:
        output = response.get("output")
        if not isinstance(output, list):
            raise OpenAIResponsesAdapter._invalid_response(
                "response output must be a list"
            )
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "message":
                continue
            contents = item.get("content")
            if not isinstance(contents, list):
                continue
            for content_item in contents:
                if not isinstance(content_item, Mapping):
                    continue
                if content_item.get("type") == "refusal":
                    refusal = content_item.get("refusal", "model refused the request")
                    raise ModelProviderError(
                        str(refusal),
                        code=ModelErrorCode.REFUSAL,
                        retriable=False,
                    )
                if content_item.get("type") == "output_text" and isinstance(
                    content_item.get("text"), str
                ):
                    text_parts.append(content_item["text"])
        content = "".join(text_parts)
        if not content:
            raise OpenAIResponsesAdapter._invalid_response(
                "response contains no output_text content"
            )
        return content

    @staticmethod
    def _invalid_response(message: str) -> ModelProviderError:
        return ModelProviderError(
            message,
            code=ModelErrorCode.INVALID_RESPONSE,
            retriable=False,
        )

    @staticmethod
    def _http_transport(
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        return post_json(
            url,
            headers,
            payload,
            timeout_seconds,
            provider_name="OpenAI",
            error_mapper=OpenAIResponsesAdapter._map_http_error,
        )

    @staticmethod
    def _map_http_error(
        status_code: int,
        payload: Mapping[str, Any],
    ) -> ModelProviderError:
        error_payload = payload.get("error")
        if not isinstance(error_payload, Mapping):
            error_payload = {}
        provider_code = error_payload.get("code")
        message = error_payload.get("message")
        if not isinstance(message, str) or not message:
            message = f"OpenAI HTTP {status_code}"

        if status_code == 401:
            code, retriable = ModelErrorCode.AUTHENTICATION, False
        elif status_code == 403:
            code, retriable = ModelErrorCode.PERMISSION, False
        elif status_code == 429 and provider_code in _NON_RETRIABLE_QUOTA_CODES:
            code, retriable = ModelErrorCode.QUOTA, False
        elif status_code == 429:
            code, retriable = ModelErrorCode.RATE_LIMIT, True
        elif status_code in {408, 409}:
            code, retriable = ModelErrorCode.TIMEOUT, True
        elif status_code >= 500:
            code, retriable = ModelErrorCode.SERVICE_UNAVAILABLE, True
        else:
            code, retriable = ModelErrorCode.INVALID_REQUEST, False
        return ModelProviderError(
            message,
            code=code,
            retriable=retriable,
            status_code=status_code,
        )
