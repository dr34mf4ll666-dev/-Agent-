"""Shared JSON-over-HTTP implementation used inside real model adapters."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from .model_gateway import ModelErrorCode, ModelProviderError


JsonHttpTransport = Callable[
    [str, Mapping[str, str], Mapping[str, Any], float],
    Mapping[str, Any],
]
HttpErrorMapper = Callable[[int, Mapping[str, Any]], ModelProviderError]


def post_json(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_seconds: float,
    *,
    provider_name: str,
    error_mapper: HttpErrorMapper,
) -> Mapping[str, Any]:
    """POST JSON and normalize transport failures without exposing credentials."""

    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return decode_json(response.read(), provider_name=provider_name)
    except urllib.error.HTTPError as error:
        try:
            error_payload = decode_json(error.read(), provider_name=provider_name)
        except ModelProviderError:
            error_payload = {}
        raise error_mapper(error.code, error_payload) from error
    except (socket.timeout, TimeoutError) as error:
        raise ModelProviderError(
            f"{provider_name} request timed out",
            code=ModelErrorCode.TIMEOUT,
            retriable=True,
        ) from error
    except urllib.error.URLError as error:
        raise ModelProviderError(
            f"{provider_name} connection failed: {error.reason}",
            code=ModelErrorCode.TRANSPORT,
            retriable=True,
        ) from error


def decode_json(raw: bytes, *, provider_name: str) -> Mapping[str, Any]:
    """Decode a provider response and require a top-level JSON object."""

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelProviderError(
            f"{provider_name} returned a non-JSON response",
            code=ModelErrorCode.INVALID_RESPONSE,
            retriable=False,
        ) from error
    if not isinstance(payload, Mapping):
        raise ModelProviderError(
            f"{provider_name} response body must be a JSON object",
            code=ModelErrorCode.INVALID_RESPONSE,
            retriable=False,
        )
    return payload
