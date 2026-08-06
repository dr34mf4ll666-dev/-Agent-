"""DeepSeek Chat Completions adapter for the unified Model Gateway."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from .model_gateway import (
    ModelAdapterResponse,
    ModelErrorCode,
    ModelGatewayConfigurationError,
    ModelProviderError,
    ModelRequest,
    ModelUsage,
)
from .model_http import JsonHttpTransport, post_json


DeepSeekTransport = JsonHttpTransport


class DeepSeekChatAdapter:
    """Translate the stable model interface to DeepSeek Chat Completions."""

    provider = "deepseek"

    def __init__(
        self,
        *,
        model: str,
        env: Mapping[str, str] | None = None,
        base_url: str = "https://api.deepseek.com",
        transport: DeepSeekTransport | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ModelGatewayConfigurationError(
                "DeepSeek model must be a non-empty string"
            )
        environment = os.environ if env is None else env
        api_key = environment.get("DEEPSEEK_API_KEY", "")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ModelGatewayConfigurationError(
                "DEEPSEEK_API_KEY is required for the DeepSeek adapter"
            )
        if not isinstance(base_url, str) or not base_url.startswith(
            ("http://", "https://")
        ):
            raise ModelGatewayConfigurationError(
                "DeepSeek base_url must be an HTTP(S) URL"
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
        base_url: str = "https://api.deepseek.com",
        transport: DeepSeekTransport | None = None,
    ) -> DeepSeekChatAdapter:
        return cls(
            model=model,
            env=env,
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
            "messages": self._build_messages(request),
            "max_tokens": request.max_output_tokens,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        if request.response_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        response = self._transport(
            f"{self._base_url}/chat/completions",
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload,
            timeout_seconds,
        )
        return self._parse_response(
            response,
            structured=request.response_schema is not None,
        )

    @staticmethod
    def _build_messages(request: ModelRequest) -> list[dict[str, str]]:
        system_parts: list[str] = []
        if request.system_prompt is not None:
            system_parts.append(request.system_prompt)
        if request.response_schema is not None:
            schema_text = json.dumps(
                request.response_schema,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            system_parts.append(
                "Return only a valid JSON object matching this JSON Schema. "
                f"Schema name: {request.schema_name}. JSON Schema: {schema_text}"
            )

        messages: list[dict[str, str]] = []
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})
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
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise self._invalid_response("response choices must be a non-empty list")
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise self._invalid_response("response choice must be a JSON object")

        finish_reason = choice.get("finish_reason")
        if finish_reason == "insufficient_system_resource":
            raise ModelProviderError(
                "DeepSeek stopped because inference resources were insufficient",
                code=ModelErrorCode.SERVICE_UNAVAILABLE,
                retriable=True,
            )
        if finish_reason == "content_filter":
            raise ModelProviderError(
                "DeepSeek content filter rejected the response",
                code=ModelErrorCode.REFUSAL,
                retriable=False,
            )
        if finish_reason != "stop":
            raise self._invalid_response(
                f"unexpected DeepSeek finish_reason: {finish_reason!r}"
            )

        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise self._invalid_response("response message must be a JSON object")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise self._invalid_response("response contains no message content")

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
                input_tokens=usage_payload["prompt_tokens"],
                output_tokens=usage_payload["completion_tokens"],
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
            provider_name="DeepSeek",
            error_mapper=DeepSeekChatAdapter._map_http_error,
        )

    @staticmethod
    def _map_http_error(
        status_code: int,
        payload: Mapping[str, Any],
    ) -> ModelProviderError:
        error_payload = payload.get("error")
        if not isinstance(error_payload, Mapping):
            error_payload = {}
        message = error_payload.get("message")
        if not isinstance(message, str) or not message:
            message = f"DeepSeek HTTP {status_code}"

        if status_code == 401:
            code, retriable = ModelErrorCode.AUTHENTICATION, False
        elif status_code == 402:
            code, retriable = ModelErrorCode.QUOTA, False
        elif status_code == 403:
            code, retriable = ModelErrorCode.PERMISSION, False
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
