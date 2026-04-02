from __future__ import annotations

from dataclasses import dataclass


MATCH_THRESHOLD = 0.75
SOURCE_OWNER_BONUS = 0.10
SOURCE_OWNER_CONFLICT_PENALTY = 0.30


@dataclass(frozen=True, slots=True)
class SpeakerMatch:
    speaker_name: str | None
    confidence: float
    source_owner: str | None
    pyannote_match: str | None
    pyannote_confidence: float | None


def choose_speaker(
    *,
    source_owner: str | None,
    pyannote_match: str | None,
    confidence: float | int | None,
) -> SpeakerMatch:
    normalized_confidence = _normalize_confidence(confidence)
    combined_confidence = _combine_confidence(
        source_owner=source_owner,
        pyannote_match=pyannote_match,
        pyannote_confidence=normalized_confidence,
    )

    return SpeakerMatch(
        speaker_name=_final_speaker_name(
            source_owner=source_owner,
            pyannote_match=pyannote_match,
            combined_confidence=combined_confidence,
        ),
        confidence=combined_confidence,
        source_owner=source_owner,
        pyannote_match=pyannote_match,
        pyannote_confidence=normalized_confidence,
    )


def _normalize_confidence(confidence: float | int | None) -> float | None:
    if confidence is None:
        return None

    normalized = float(confidence)
    if normalized > 1:
        normalized /= 100.0

    return round(max(0.0, min(1.0, normalized)), 2)


def _combine_confidence(
    *,
    source_owner: str | None,
    pyannote_match: str | None,
    pyannote_confidence: float | None,
) -> float:
    combined = pyannote_confidence or 0.0

    if source_owner and pyannote_match:
        if _labels_match(source_owner, pyannote_match):
            combined += SOURCE_OWNER_BONUS
        else:
            combined -= SOURCE_OWNER_CONFLICT_PENALTY

    return round(max(0.0, min(1.0, combined)), 2)


def _final_speaker_name(
    *,
    source_owner: str | None,
    pyannote_match: str | None,
    combined_confidence: float,
) -> str | None:
    if not pyannote_match or combined_confidence < MATCH_THRESHOLD:
        return None

    if source_owner and not _labels_match(source_owner, pyannote_match):
        return None

    return source_owner or pyannote_match


def _labels_match(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()
