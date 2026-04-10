from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request


DEFAULT_SPEECH_MODELS = ("universal-3-pro", "universal-2")
SPEAKER_PREFIX_PATTERN = re.compile(r"^speaker[_ -]?[A-Z0-9]+$", re.IGNORECASE)


class AssemblyAIClientError(RuntimeError):
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

    def upload_bytes(
        self,
        url: str,
        *,
        data: bytes,
        headers: dict[str, str] | None = None,
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
        return _read_json_response(http_request, timeout=timeout)

    def upload_bytes(
        self,
        url: str,
        *,
        data: bytes,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        request_headers = {"Content-Type": "application/octet-stream", **(headers or {})}
        http_request = request.Request(url, data=data, headers=request_headers, method="POST")
        return _read_json_response(http_request, timeout=timeout)


@dataclass(frozen=True, slots=True)
class AssemblyAIUtterance:
    vendor_segment_id: str | None
    text: str
    speaker_hint: str | None
    speaker_name: str | None
    confidence: float | None
    start_seconds: float
    end_seconds: float
    raw_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AssemblyAISpeakerProfile:
    name: str
    description: str | None = None
    aliases: tuple[str, ...] = ()


class AssemblyAIClient:
    def __init__(
        self,
        *,
        api_key: str,
        transport: Transport | None = None,
        base_url: str = "https://api.assemblyai.com/v2",
        poll_interval_seconds: float = 1.0,
        max_poll_attempts: int = 60,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.transport = transport or StdlibTransport()
        self.base_url = base_url.rstrip("/")
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_attempts = max_poll_attempts
        self.sleep = sleep

    def transcribe_bytes(
        self,
        audio_bytes: bytes,
        *,
        speakers: Sequence[AssemblyAISpeakerProfile],
    ) -> list[AssemblyAIUtterance]:
        speaker_profiles = tuple(speakers)
        upload_response = self.transport.upload_bytes(
            f"{self.base_url}/upload",
            data=audio_bytes,
            headers={
                **self._headers(),
                "Content-Type": "application/octet-stream",
            },
        )
        upload_url = _required_string(upload_response, "upload_url", "upload response")
        transcript_response = self.transport.request_json(
            "POST",
            f"{self.base_url}/transcript",
            headers=self._headers(),
            payload=self._transcript_payload(upload_url=upload_url, speakers=speaker_profiles),
        )
        transcript_id = _required_string(transcript_response, "id", "transcript create response")
        completed_response = self._poll_until_complete(
            transcript_id=transcript_id,
            initial_response=transcript_response,
        )
        return self._parse_utterances(completed_response, speakers=speaker_profiles)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": self.api_key,
        }

    def _transcript_payload(
        self,
        *,
        upload_url: str,
        speakers: Sequence[AssemblyAISpeakerProfile],
    ) -> dict[str, Any]:
        speaker_payloads = [_speaker_payload(profile) for profile in speakers]
        payload: dict[str, Any] = {
            "audio_url": upload_url,
            "language_detection": True,
            "speaker_labels": True,
            "speech_models": list(DEFAULT_SPEECH_MODELS),
            "speech_understanding": {
                "request": {
                    "speaker_identification": {
                        "speaker_type": "name",
                        "speakers": speaker_payloads,
                    }
                }
            },
        }

        if speakers:
            payload["speakers_expected"] = len(speakers)

        return payload

    def _poll_until_complete(
        self,
        *,
        transcript_id: str,
        initial_response: Mapping[str, Any],
    ) -> dict[str, Any]:
        response = dict(initial_response)

        for attempt in range(self.max_poll_attempts):
            status = _optional_string(response.get("status"))
            if status == "completed":
                return response
            if status == "error":
                error_message = _optional_string(response.get("error")) or "unknown AssemblyAI error"
                raise AssemblyAIClientError(f"AssemblyAI transcript {transcript_id} failed: {error_message}")
            if attempt + 1 >= self.max_poll_attempts:
                break
            self.sleep(self.poll_interval_seconds)
            response = self.transport.request_json(
                "GET",
                f"{self.base_url}/transcript/{transcript_id}",
                headers=self._headers(),
            )

        raise AssemblyAIClientError(
            f"AssemblyAI transcript {transcript_id} did not complete after {self.max_poll_attempts} polls"
        )

    def _parse_utterances(
        self,
        payload: Mapping[str, Any],
        *,
        speakers: Sequence[AssemblyAISpeakerProfile],
    ) -> list[AssemblyAIUtterance]:
        utterances = payload.get("utterances")
        if not isinstance(utterances, list):
            if _is_empty_completed_transcript(payload):
                return []
            raise AssemblyAIClientError("AssemblyAI completed response must include an utterances list")

        speaker_mapping = _speaker_mapping(payload)
        speaker_lookup = _speaker_lookup(speakers)
        parsed: list[AssemblyAIUtterance] = []
        for item in utterances:
            if not isinstance(item, Mapping):
                raise AssemblyAIClientError("AssemblyAI utterances must be JSON objects")

            text = _optional_string(item.get("text"))
            if text is None:
                continue

            speaker_hint, speaker_name = _resolve_speaker(
                item.get("speaker"),
                mapping=speaker_mapping,
                speaker_lookup=speaker_lookup,
            )
            start_seconds = _milliseconds_to_seconds(item.get("start"), field_name="start")
            end_seconds = _milliseconds_to_seconds(item.get("end"), field_name="end")

            parsed.append(
                AssemblyAIUtterance(
                    vendor_segment_id=_optional_string(item.get("id")),
                    text=text,
                    speaker_hint=speaker_hint,
                    speaker_name=speaker_name,
                    confidence=_coerce_float(item.get("confidence")),
                    start_seconds=start_seconds,
                    end_seconds=max(start_seconds, end_seconds),
                    raw_payload=deepcopy(dict(item)),
                )
            )

        return parsed


def _is_empty_completed_transcript(payload: Mapping[str, Any]) -> bool:
    if _optional_string(payload.get("text")) is not None:
        return False

    words = payload.get("words")
    if words is not None and (not isinstance(words, list) or len(words) > 0):
        return False

    utterances = payload.get("utterances")
    if utterances is not None:
        return False

    return True


def _speaker_payload(profile: AssemblyAISpeakerProfile) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": profile.name}
    description = _optional_string(profile.description)
    if description is not None:
        payload["description"] = description
    aliases = [_optional_string(alias) for alias in profile.aliases]
    normalized_aliases = [alias for alias in aliases if alias is not None]
    if normalized_aliases:
        payload["aliases"] = normalized_aliases
    return payload


def _read_json_response(http_request: request.Request, *, timeout: float) -> dict[str, Any]:
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            body = response.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AssemblyAIClientError(f"AssemblyAI request failed: {exc.code} {detail}") from exc
    except error.URLError as exc:
        raise AssemblyAIClientError(f"AssemblyAI request failed: {exc.reason}") from exc

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except json.JSONDecodeError as exc:
        raise AssemblyAIClientError("AssemblyAI returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise AssemblyAIClientError("AssemblyAI response must be a JSON object")

    return payload


def _speaker_mapping(payload: Mapping[str, Any]) -> dict[str, str]:
    speaker_identification = (
        payload.get("speech_understanding", {})
        .get("response", {})
        .get("speaker_identification", {})
    )
    mapping = speaker_identification.get("mapping")
    if not isinstance(mapping, Mapping):
        return {}

    normalized: dict[str, str] = {}
    for hint, name in mapping.items():
        normalized_hint = _optional_string(hint)
        normalized_name = _optional_string(name)
        if normalized_hint is None or normalized_name is None:
            continue
        normalized[normalized_hint] = normalized_name

    return normalized


def _speaker_lookup(speakers: Sequence[AssemblyAISpeakerProfile]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for speaker in speakers:
        canonical_name = _optional_string(speaker.name)
        if canonical_name is None:
            continue
        normalized[_normalize_speaker_key(canonical_name)] = canonical_name
        for alias in speaker.aliases:
            normalized_alias = _optional_string(alias)
            if normalized_alias is None:
                continue
            normalized[_normalize_speaker_key(normalized_alias)] = canonical_name
    return normalized


def _resolve_speaker(
    raw_speaker: Any,
    *,
    mapping: Mapping[str, str],
    speaker_lookup: Mapping[str, str],
) -> tuple[str | None, str | None]:
    speaker = _optional_string(raw_speaker)
    if speaker is None:
        return None, None

    if speaker in mapping:
        return speaker, _resolve_speaker_name(mapping[speaker], speaker_lookup)

    reverse_mapping: dict[str, str] = {}
    ambiguous_names: set[str] = set()
    for hint, name in mapping.items():
        canonical_name = _resolve_speaker_name(name, speaker_lookup)
        if canonical_name is None:
            continue
        if canonical_name in reverse_mapping and reverse_mapping[canonical_name] != hint:
            ambiguous_names.add(canonical_name)
            continue
        reverse_mapping[canonical_name] = hint

    canonical_speaker = _resolve_speaker_name(speaker, speaker_lookup)
    if canonical_speaker in reverse_mapping and canonical_speaker not in ambiguous_names:
        return reverse_mapping[canonical_speaker], canonical_speaker
    if _looks_like_diarization_label(speaker):
        return speaker, None

    return None, canonical_speaker


def _resolve_speaker_name(raw_name: str, speaker_lookup: Mapping[str, str]) -> str | None:
    normalized_name = _optional_string(raw_name)
    if normalized_name is None or _looks_like_diarization_label(normalized_name):
        return None
    return speaker_lookup.get(_normalize_speaker_key(normalized_name))


def _normalize_speaker_key(value: str) -> str:
    return value.strip().casefold()


def _milliseconds_to_seconds(value: Any, *, field_name: str) -> float:
    seconds = _coerce_float(value)
    if seconds is None:
        raise AssemblyAIClientError(f"AssemblyAI utterance missing numeric {field_name}")
    return seconds / 1000.0


def _required_string(payload: Mapping[str, Any], field_name: str, context: str) -> str:
    value = _optional_string(payload.get(field_name))
    if value is None:
        raise AssemblyAIClientError(f"AssemblyAI {context} missing {field_name}")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _looks_like_diarization_label(value: str) -> bool:
    if SPEAKER_PREFIX_PATTERN.match(value):
        return True
    return value.isalnum() and value.upper() == value and len(value) <= 3
