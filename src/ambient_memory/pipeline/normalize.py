from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any


DEEPGRAM_VENDOR = "deepgram"


@dataclass(slots=True, frozen=True)
class NormalizedTranscriptSegment:
    source_id: str
    vendor: str
    vendor_segment_id: str | None
    text: str
    speaker_hint: str | None
    speaker_confidence: float | None
    confidence: float | None
    started_at: datetime
    ended_at: datetime
    raw_payload: dict[str, Any]


def normalize_deepgram_response(
    payload: Mapping[str, Any],
    *,
    source_id: str,
    chunk_started_at: datetime,
) -> list[NormalizedTranscriptSegment]:
    normalized_started_at = _normalize_chunk_started_at(chunk_started_at)
    utterances = payload.get("results", {}).get("utterances", [])

    if not isinstance(utterances, list):
        return []

    segments: list[NormalizedTranscriptSegment] = []
    for utterance in utterances:
        if not isinstance(utterance, Mapping):
            continue

        text = str(utterance.get("transcript", "")).strip()
        if not text:
            continue

        segments.append(
            NormalizedTranscriptSegment(
                source_id=source_id,
                vendor=DEEPGRAM_VENDOR,
                vendor_segment_id=_optional_string(utterance.get("id")),
                text=text,
                speaker_hint=_speaker_hint(utterance.get("speaker")),
                speaker_confidence=_speaker_confidence(utterance),
                confidence=_confidence(utterance),
                started_at=normalized_started_at + timedelta(seconds=float(utterance.get("start", 0.0))),
                ended_at=normalized_started_at + timedelta(seconds=float(utterance.get("end", 0.0))),
                raw_payload=deepcopy(dict(utterance)),
            )
        )

    return segments


def _normalize_chunk_started_at(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("chunk_started_at must be timezone-aware")

    return value.astimezone(UTC)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def _speaker_hint(value: Any) -> str | None:
    speaker = _optional_string(value)
    if speaker is None:
        return None

    if speaker.startswith("speaker_"):
        return speaker

    return f"speaker_{speaker}"


def _speaker_confidence(utterance: Mapping[str, Any]) -> float | None:
    utterance_confidence = utterance.get("speaker_confidence")
    if isinstance(utterance_confidence, int | float):
        return float(utterance_confidence)

    speaker = utterance.get("speaker")
    word_confidences: list[float] = []

    for word in utterance.get("words", []):
        if not isinstance(word, Mapping):
            continue

        confidence = word.get("speaker_confidence")
        if not isinstance(confidence, int | float):
            continue

        if speaker is not None and word.get("speaker") not in (None, speaker):
            continue

        word_confidences.append(float(confidence))

    if not word_confidences:
        return None

    return mean(word_confidences)


def _confidence(utterance: Mapping[str, Any]) -> float | None:
    value = utterance.get("confidence")
    if isinstance(value, int | float):
        return float(value)

    word_confidences = [
        float(word["confidence"])
        for word in utterance.get("words", [])
        if isinstance(word, Mapping) and isinstance(word.get("confidence"), int | float)
    ]
    if not word_confidences:
        return None

    return mean(word_confidences)
