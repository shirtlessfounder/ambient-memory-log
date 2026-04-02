from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from typing import Any, Protocol, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_QUERY_PARAMS = {
    "model": "nova-3",
    "diarize": "true",
    "utterances": "true",
    "punctuate": "true",
    "smart_format": "true",
}


class ResponseLike(Protocol):
    def read(self) -> bytes: ...

    def close(self) -> None: ...


Transport: TypeAlias = Callable[[Request], ResponseLike]


class DeepgramClientError(RuntimeError):
    pass


class DeepgramClient:
    def __init__(
        self,
        *,
        api_key: str,
        transport: Transport | None = None,
        base_url: str = "https://api.deepgram.com",
        timeout: float = 30.0,
        query_params: Mapping[str, str | int | float | bool] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport or _build_default_transport(timeout)
        self._query_params = {
            **DEFAULT_QUERY_PARAMS,
            **_stringify_query_params(query_params or {}),
        }

    def transcribe_bytes(self, audio_bytes: bytes, *, content_type: str = "audio/wav") -> dict[str, Any]:
        return self._post(body=audio_bytes, content_type=content_type)

    def transcribe_url(self, audio_url: str) -> dict[str, Any]:
        body = json.dumps({"url": audio_url}).encode("utf-8")
        return self._post(body=body, content_type="application/json")

    def _post(self, *, body: bytes, content_type: str) -> dict[str, Any]:
        request = Request(
            url=self._build_url(),
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Token {self._api_key}",
                "Content-Type": content_type,
            },
        )

        try:
            response = self._transport(request)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise DeepgramClientError(f"Deepgram request failed with status {exc.code}: {detail}") from exc
        except URLError as exc:
            raise DeepgramClientError(f"Deepgram request failed: {exc.reason}") from exc

        try:
            payload = json.loads(response.read().decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DeepgramClientError("Deepgram returned invalid JSON") from exc
        finally:
            response.close()

        if not isinstance(payload, dict):
            raise DeepgramClientError("Deepgram response must be a JSON object")

        return payload

    def _build_url(self) -> str:
        return f"{self._base_url}/v1/listen?{urlencode(self._query_params)}"


def _build_default_transport(timeout: float) -> Transport:
    def _transport(request: Request) -> ResponseLike:
        return urlopen(request, timeout=timeout)

    return _transport


def _stringify_query_params(params: Mapping[str, str | int | float | bool]) -> dict[str, str]:
    normalized: dict[str, str] = {}

    for key, value in params.items():
        if isinstance(value, bool):
            normalized[key] = str(value).lower()
            continue

        normalized[key] = str(value)

    return normalized
