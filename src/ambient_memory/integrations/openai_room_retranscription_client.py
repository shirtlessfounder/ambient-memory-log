from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


DEFAULT_OPENAI_AUDIO_TRANSCRIPTION_MODEL = "gpt-4o-transcribe-diarize"


class ResponseLike(Protocol):
    def read(self) -> bytes: ...

    def close(self) -> None: ...


Transport: TypeAlias = Callable[[Request], ResponseLike]


class OpenAIRoomRetranscriptionClientError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RoomRetranscribedSegment:
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None = None


class OpenAIRoomRetranscriptionClient:
    vendor = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        transport: Transport | None = None,
        model: str = DEFAULT_OPENAI_AUDIO_TRANSCRIPTION_MODEL,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 900.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport or _build_default_transport(timeout)

    def transcribe_window(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        window_started_at: datetime,
        content_type: str = "audio/wav",
    ) -> list[RoomRetranscribedSegment]:
        del window_started_at

        request = _build_transcription_request(
            api_key=self.api_key,
            url=f"{self.base_url}/audio/transcriptions",
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
            model=self.model,
        )
        payload = _read_json_response(self.transport, request)
        return _parse_segments(payload)


def _build_default_transport(timeout: float) -> Transport:
    def _transport(http_request: Request) -> ResponseLike:
        return urlopen(http_request, timeout=timeout)

    return _transport


def _build_transcription_request(
    *,
    api_key: str,
    url: str,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    model: str,
) -> Request:
    boundary = f"ambient-memory-{uuid4().hex}"
    fields = (
        ("model", model),
        ("response_format", _response_format_for_model(model)),
        *_optional_chunking_strategy_field(model),
    )
    body = _multipart_body(
        boundary=boundary,
        fields=fields,
        file_field_name="file",
        filename=filename,
        content_type=content_type,
        file_bytes=audio_bytes,
    )
    return Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )


def _read_json_response(transport: Transport, http_request: Request) -> dict[str, Any]:
    try:
        response = transport(http_request)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenAIRoomRetranscriptionClientError(
            f"OpenAI audio transcription request failed with status {exc.code}: {detail}"
        ) from exc
    except TimeoutError as exc:
        raise OpenAIRoomRetranscriptionClientError(
            f"OpenAI audio transcription request timed out: {exc}"
        ) from exc
    except URLError as exc:
        raise OpenAIRoomRetranscriptionClientError(
            f"OpenAI audio transcription request failed: {exc.reason}"
        ) from exc

    try:
        body = response.read()
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise OpenAIRoomRetranscriptionClientError(
            "OpenAI audio transcription response was not valid JSON"
        ) from exc
    finally:
        response.close()

    if not isinstance(payload, dict):
        raise OpenAIRoomRetranscriptionClientError("OpenAI audio transcription response must be a JSON object")
    if "error" in payload:
        raise OpenAIRoomRetranscriptionClientError(f"OpenAI audio transcription failed: {payload['error']}")
    return payload


def _parse_segments(payload: Mapping[str, Any]) -> list[RoomRetranscribedSegment]:
    segments_value = payload.get("segments")
    if not isinstance(segments_value, list):
        raise OpenAIRoomRetranscriptionClientError("OpenAI audio transcription response must include a segments list")

    parsed: list[RoomRetranscribedSegment] = []
    for item in segments_value:
        if not isinstance(item, Mapping):
            raise OpenAIRoomRetranscriptionClientError("OpenAI audio transcription segments must be JSON objects")

        start_seconds = _required_float(item, "start", context="segment")
        end_seconds = _required_float(item, "end", context="segment")
        if end_seconds < start_seconds:
            raise OpenAIRoomRetranscriptionClientError("OpenAI audio transcription segment end_seconds must be >= start_seconds")

        text = _optional_text(item.get("text"))
        if text is None:
            continue

        parsed.append(
            RoomRetranscribedSegment(
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                text=text,
                confidence=_optional_float(item.get("confidence"), field_name="confidence"),
            )
        )

    return parsed


def _response_format_for_model(model: str) -> str:
    if "diarize" in model:
        return "diarized_json"
    return "verbose_json"


def _optional_chunking_strategy_field(model: str) -> tuple[tuple[str, str], ...]:
    if "diarize" in model:
        return (("chunking_strategy", "auto"),)
    return ()


def _multipart_body(
    *,
    boundary: str,
    fields: tuple[tuple[str, str], ...],
    file_field_name: str,
    filename: str,
    content_type: str,
    file_bytes: bytes,
) -> bytes:
    chunks: list[bytes] = []

    for name, value in fields:
        chunks.extend(
            (
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            )
        )

    chunks.extend(
        (
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field_name}"; filename="{filename}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        )
    )
    return b"".join(chunks)


def _required_text(payload: Mapping[str, Any], field_name: str, *, context: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise OpenAIRoomRetranscriptionClientError(
            f"OpenAI audio transcription {context} field {field_name} must be a string"
        )

    normalized = value.strip()
    if not normalized:
        raise OpenAIRoomRetranscriptionClientError(
            f"OpenAI audio transcription {context} field {field_name} must not be empty"
        )
    return normalized


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        raise OpenAIRoomRetranscriptionClientError(
            "OpenAI audio transcription segment field text must be a string"
        )
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _required_float(payload: Mapping[str, Any], field_name: str, *, context: str) -> float:
    value = payload.get(field_name)
    if not isinstance(value, int | float):
        raise OpenAIRoomRetranscriptionClientError(
            f"OpenAI audio transcription {context} field {field_name} must be numeric"
        )
    return float(value)


def _optional_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float):
        raise OpenAIRoomRetranscriptionClientError(
            f"OpenAI audio transcription segment field {field_name} must be numeric"
        )
    return float(value)
