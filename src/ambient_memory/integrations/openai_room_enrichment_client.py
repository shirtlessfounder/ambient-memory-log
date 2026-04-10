from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any, Protocol
from urllib import error, request

from ambient_memory.pipeline.room_enrichment import (
    RoomEnrichmentSpeakerResolution,
    RoomEnrichmentTextCleanup,
    RoomEnrichmentUtterance,
)


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"

SPEAKER_RESOLUTION_SYSTEM_PROMPT = (
    "You resolve speaker identity for room transcript utterances. "
    "Return exactly one output row for each input utterance, in the same order. "
    "Allowed resolved speaker names are only Dylan, Niyant, Alex, Jakub, or unknown. "
    "Never merge, split, add, delete, or reorder utterances. "
    "Use unknown when evidence is weak."
)

TEXT_CLEANUP_SYSTEM_PROMPT = (
    "You clean raw ASR text for room transcript utterances. "
    "Return exactly one output row for each input utterance, in the same order. "
    "Keep wording close to the source, fix only obvious ASR errors, and do not summarize or stylize. "
    "Never merge, split, add, delete, or reorder utterances."
)


class OpenAIRoomEnrichmentClientError(RuntimeError):
    pass


class Transport(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]: ...


class StdlibTransport:
    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        request_headers = dict(headers or {})
        data: bytes | None = None

        if payload is not None:
            request_headers.setdefault("Content-Type", "application/json")
            data = json.dumps(payload).encode("utf-8")

        http_request = request.Request(url, data=data, headers=request_headers, method=method.upper())
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                body = response.read()
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise OpenAIRoomEnrichmentClientError(
                f"OpenAI API request failed with status {exc.code}: {body}"
            ) from exc
        except error.URLError as exc:
            raise OpenAIRoomEnrichmentClientError(f"OpenAI API request failed: {exc.reason}") from exc

        try:
            parsed = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise OpenAIRoomEnrichmentClientError("OpenAI API response was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise OpenAIRoomEnrichmentClientError("OpenAI API response must be a JSON object")
        return parsed


class OpenAIRoomEnrichmentClient:
    vendor = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        transport: Transport | None = None,
        model: str = DEFAULT_OPENAI_MODEL,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.transport = transport or StdlibTransport()
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def resolve_speakers(
        self,
        utterances: Sequence[RoomEnrichmentUtterance],
        *,
        allowed_speakers: tuple[str, ...],
    ) -> list[RoomEnrichmentSpeakerResolution]:
        payload = {
            "allowed_speakers": list(allowed_speakers),
            "utterances": [_serialize_utterance(utterance) for utterance in utterances],
        }
        response = self._request_completion(
            system_prompt=SPEAKER_RESOLUTION_SYSTEM_PROMPT,
            user_payload=payload,
            schema_name="room_speaker_resolution",
            schema=_speaker_resolution_schema(allowed_speakers),
        )
        rows = _parse_output_rows(response)
        parsed = [
            RoomEnrichmentSpeakerResolution(
                canonical_utterance_id=_required_string(row, "canonical_utterance_id"),
                resolved_speaker_name=_required_string(row, "resolved_speaker_name"),
                resolved_speaker_confidence=_required_float(row, "resolved_speaker_confidence"),
                resolution_notes=_optional_string(row.get("resolution_notes")),
            )
            for row in rows
        ]
        _validate_row_preserving(utterances, parsed, stage_name="speaker resolution")
        if any(row.resolved_speaker_name not in allowed_speakers for row in parsed):
            raise OpenAIRoomEnrichmentClientError("speaker resolution returned a disallowed speaker name")
        return parsed

    def cleanup_text(
        self,
        utterances: Sequence[RoomEnrichmentUtterance],
        *,
        speaker_resolutions: Sequence[RoomEnrichmentSpeakerResolution],
    ) -> list[RoomEnrichmentTextCleanup]:
        payload = {
            "utterances": [_serialize_utterance(utterance) for utterance in utterances],
            "speaker_resolutions": [
                {
                    "canonical_utterance_id": row.canonical_utterance_id,
                    "resolved_speaker_name": row.resolved_speaker_name,
                    "resolved_speaker_confidence": row.resolved_speaker_confidence,
                    "resolution_notes": row.resolution_notes,
                }
                for row in speaker_resolutions
            ],
        }
        response = self._request_completion(
            system_prompt=TEXT_CLEANUP_SYSTEM_PROMPT,
            user_payload=payload,
            schema_name="room_text_cleanup",
            schema=_text_cleanup_schema(),
        )
        rows = _parse_output_rows(response)
        parsed = [
            RoomEnrichmentTextCleanup(
                canonical_utterance_id=_required_string(row, "canonical_utterance_id"),
                cleaned_text=_required_string(row, "cleaned_text"),
                cleaned_text_confidence=_required_float(row, "cleaned_text_confidence"),
            )
            for row in rows
        ]
        _validate_row_preserving(utterances, parsed, stage_name="text cleanup")
        return parsed

    def _request_completion(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        response = self.transport.request_json(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            payload={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                },
            },
            timeout=self.timeout,
        )
        if "error" in response:
            raise OpenAIRoomEnrichmentClientError(f"OpenAI completion failed: {response['error']}")
        return response


def _serialize_utterance(utterance: RoomEnrichmentUtterance) -> dict[str, Any]:
    return {
        "canonical_utterance_id": utterance.canonical_utterance_id,
        "started_at": utterance.started_at.isoformat(),
        "ended_at": utterance.ended_at.isoformat(),
        "raw_text": utterance.raw_text,
        "current_speaker_label": utterance.current_speaker_label,
    }


def _speaker_resolution_schema(allowed_speakers: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "utterances": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "canonical_utterance_id": {"type": "string"},
                        "resolved_speaker_name": {"type": "string", "enum": list(allowed_speakers)},
                        "resolved_speaker_confidence": {"type": "number"},
                        "resolution_notes": {"type": "string"},
                    },
                    "required": [
                        "canonical_utterance_id",
                        "resolved_speaker_name",
                        "resolved_speaker_confidence",
                        "resolution_notes",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["utterances"],
        "additionalProperties": False,
    }


def _text_cleanup_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "utterances": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "canonical_utterance_id": {"type": "string"},
                        "cleaned_text": {"type": "string"},
                        "cleaned_text_confidence": {"type": "number"},
                    },
                    "required": [
                        "canonical_utterance_id",
                        "cleaned_text",
                        "cleaned_text_confidence",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["utterances"],
        "additionalProperties": False,
    }


def _parse_output_rows(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenAIRoomEnrichmentClientError("OpenAI completion must include choices")
    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        raise OpenAIRoomEnrichmentClientError("OpenAI completion choice must be an object")
    message = first_choice.get("message")
    if not isinstance(message, Mapping):
        raise OpenAIRoomEnrichmentClientError("OpenAI completion choice must include a message")
    content = _extract_message_content(message.get("content"))
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise OpenAIRoomEnrichmentClientError("OpenAI completion content was not valid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise OpenAIRoomEnrichmentClientError("OpenAI completion JSON must be an object")
    utterances = parsed.get("utterances")
    if not isinstance(utterances, list):
        raise OpenAIRoomEnrichmentClientError("OpenAI completion JSON must include an utterances list")
    if any(not isinstance(row, dict) for row in utterances):
        raise OpenAIRoomEnrichmentClientError("OpenAI completion utterances must be JSON objects")
    return utterances


def _extract_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            text = item.get("text")
            if isinstance(text, str):
                texts.append(text)
        if texts:
            return "".join(texts)
    raise OpenAIRoomEnrichmentClientError("OpenAI completion message content must be text")


def _validate_row_preserving(
    utterances: Sequence[RoomEnrichmentUtterance],
    output_rows: Sequence[RoomEnrichmentSpeakerResolution | RoomEnrichmentTextCleanup],
    *,
    stage_name: str,
) -> None:
    expected_ids = [utterance.canonical_utterance_id for utterance in utterances]
    actual_ids = [row.canonical_utterance_id for row in output_rows]
    if actual_ids != expected_ids:
        raise OpenAIRoomEnrichmentClientError(
            f"{stage_name} must stay row-preserving; expected {expected_ids}, got {actual_ids}"
        )


def _required_string(row: Mapping[str, Any], field_name: str) -> str:
    value = row.get(field_name)
    if not isinstance(value, str):
        raise OpenAIRoomEnrichmentClientError(f"OpenAI completion field {field_name} must be a string")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OpenAIRoomEnrichmentClientError("OpenAI completion optional string field must be a string")
    return value


def _required_float(row: Mapping[str, Any], field_name: str) -> float:
    value = row.get(field_name)
    if not isinstance(value, int | float):
        raise OpenAIRoomEnrichmentClientError(f"OpenAI completion field {field_name} must be numeric")
    return float(value)
