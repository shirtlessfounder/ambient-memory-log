from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ambient_memory.integrations.openai_room_retranscription_client import RoomRetranscribedSegment


@dataclass(frozen=True, slots=True)
class CanonicalUtteranceWindowRow:
    canonical_utterance_id: str
    started_at: datetime
    ended_at: datetime
    raw_text: str


@dataclass(frozen=True, slots=True)
class AlignedTranscriptRow:
    canonical_utterance_id: str
    text: str
    confidence: float | None
    used_raw_fallback: bool


def align_retranscribed_segments(
    utterances: Sequence[CanonicalUtteranceWindowRow],
    segments: Sequence[RoomRetranscribedSegment],
    *,
    window_started_at: datetime,
) -> list[AlignedTranscriptRow]:
    if not utterances:
        return []

    normalized_window_started_at = _normalize_timestamp(window_started_at)
    spans = [
        _relative_span(
            utterance.started_at,
            utterance.ended_at,
            window_started_at=normalized_window_started_at,
        )
        for utterance in utterances
    ]
    overlap_matrix = [
        [
            _overlap_seconds(
                utterance_start=start_seconds,
                utterance_end=end_seconds,
                segment_start=segment.start_seconds,
                segment_end=segment.end_seconds,
            )
            for segment in segments
        ]
        for start_seconds, end_seconds in spans
    ]
    utterance_candidates = _unique_best_indices(overlap_matrix)
    segment_candidates = _unique_best_indices(_transpose(overlap_matrix))

    aligned_rows: list[AlignedTranscriptRow] = []
    for index, utterance in enumerate(utterances):
        segment_index = utterance_candidates[index]
        if segment_index is None or segment_candidates[segment_index] != index:
            aligned_rows.append(
                AlignedTranscriptRow(
                    canonical_utterance_id=utterance.canonical_utterance_id,
                    text=utterance.raw_text,
                    confidence=None,
                    used_raw_fallback=True,
                )
            )
            continue

        segment = segments[segment_index]
        aligned_rows.append(
            AlignedTranscriptRow(
                canonical_utterance_id=utterance.canonical_utterance_id,
                text=segment.text,
                confidence=segment.confidence,
                used_raw_fallback=False,
            )
        )

    return aligned_rows


def _relative_span(
    started_at: datetime,
    ended_at: datetime,
    *,
    window_started_at: datetime,
) -> tuple[float, float]:
    normalized_started_at = _normalize_timestamp(started_at)
    normalized_ended_at = _normalize_timestamp(ended_at)
    start_seconds = max(0.0, (normalized_started_at - window_started_at).total_seconds())
    end_seconds = max(start_seconds, (normalized_ended_at - window_started_at).total_seconds())
    return start_seconds, end_seconds


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _overlap_seconds(
    *,
    utterance_start: float,
    utterance_end: float,
    segment_start: float,
    segment_end: float,
) -> float:
    return max(0.0, min(utterance_end, segment_end) - max(utterance_start, segment_start))


def _unique_best_indices(matrix: Sequence[Sequence[float]]) -> list[int | None]:
    best_indices: list[int | None] = []

    for row in matrix:
        if not row:
            best_indices.append(None)
            continue

        best_overlap = max(row)
        if best_overlap <= 0:
            best_indices.append(None)
            continue

        matching_indices = [index for index, overlap in enumerate(row) if overlap == best_overlap]
        if len(matching_indices) != 1:
            best_indices.append(None)
            continue

        best_indices.append(matching_indices[0])

    return best_indices


def _transpose(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    if not matrix:
        return []
    if not matrix[0]:
        return [[] for _ in range(0)]
    return [list(column) for column in zip(*matrix, strict=False)]
