from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Iterable

from sqlalchemy.orm import Session

from ambient_memory.models import CanonicalUtterance, UtteranceSource


LOCAL_SOURCE_BONUS = 0.15
NAMED_SPEAKER_BONUS = 0.05
DEDUP_TOLERANCE_SECONDS = 1.5
NORMALIZED_TEXT_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class DedupCandidate:
    transcript_candidate_id: str
    source_id: str
    source_owner: str | None
    text: str
    started_at: datetime
    ended_at: datetime
    speaker_name: str | None
    speaker_confidence: float | None
    confidence: float | None


@dataclass(frozen=True, slots=True)
class MergedUtterance:
    canonical_candidate_id: str
    canonical_source_id: str | None
    text: str
    started_at: datetime
    ended_at: datetime
    speaker_name: str | None
    speaker_confidence: float | None
    transcript_candidate_ids: tuple[str, ...]


def merge_transcript_candidates(candidates: Iterable[DedupCandidate]) -> list[MergedUtterance]:
    ordered_candidates = sorted(
        candidates,
        key=lambda candidate: (
            candidate.started_at,
            candidate.ended_at,
            candidate.source_id,
            candidate.transcript_candidate_id,
        ),
    )
    if not ordered_candidates:
        return []

    groups: list[list[DedupCandidate]] = []
    for candidate in ordered_candidates:
        for group in groups:
            if _matches_group(candidate, group):
                group.append(candidate)
                break
        else:
            groups.append([candidate])

    return [_merge_group(group) for group in groups]


def persist_canonical_utterances(
    session: Session,
    utterances: Iterable[MergedUtterance],
    *,
    processing_version: str = "v1",
) -> list[CanonicalUtterance]:
    persisted: list[CanonicalUtterance] = []

    for utterance in utterances:
        row = CanonicalUtterance(
            text=utterance.text,
            started_at=utterance.started_at,
            ended_at=utterance.ended_at,
            speaker_name=utterance.speaker_name,
            speaker_confidence=utterance.speaker_confidence,
            canonical_source_id=utterance.canonical_source_id,
            processing_version=processing_version,
        )
        session.add(row)
        session.flush()

        for transcript_candidate_id in utterance.transcript_candidate_ids:
            session.add(
                UtteranceSource(
                    canonical_utterance_id=row.id,
                    transcript_candidate_id=transcript_candidate_id,
                    is_canonical=transcript_candidate_id == utterance.canonical_candidate_id,
                )
            )

        persisted.append(row)

    session.flush()
    return persisted


def _matches_group(candidate: DedupCandidate, group: list[DedupCandidate]) -> bool:
    tolerance = timedelta(seconds=DEDUP_TOLERANCE_SECONDS)
    normalized_text = _normalize_text(candidate.text)

    for existing in group:
        if _normalize_text(existing.text) != normalized_text:
            continue
        if candidate.started_at <= existing.ended_at + tolerance and candidate.ended_at >= existing.started_at - tolerance:
            return True

    return False


def _merge_group(group: list[DedupCandidate]) -> MergedUtterance:
    canonical = min(
        group,
        key=lambda candidate: (
            -_candidate_score(candidate),
            candidate.started_at,
            candidate.ended_at,
            candidate.source_id,
            candidate.transcript_candidate_id,
        ),
    )
    transcript_candidate_ids = tuple(
        candidate.transcript_candidate_id
        for candidate in sorted(
            group,
            key=lambda item: (
                item.started_at,
                item.ended_at,
                item.source_id,
                item.transcript_candidate_id,
            ),
        )
    )

    return MergedUtterance(
        canonical_candidate_id=canonical.transcript_candidate_id,
        canonical_source_id=canonical.source_id,
        text=canonical.text,
        started_at=min(candidate.started_at for candidate in group),
        ended_at=max(candidate.ended_at for candidate in group),
        speaker_name=canonical.speaker_name,
        speaker_confidence=canonical.speaker_confidence,
        transcript_candidate_ids=transcript_candidate_ids,
    )


def _candidate_score(candidate: DedupCandidate) -> float:
    score = candidate.confidence or 0.0

    if candidate.speaker_confidence is not None:
        score += candidate.speaker_confidence / 2
    if candidate.source_owner:
        score += LOCAL_SOURCE_BONUS
    if candidate.speaker_name:
        score += NAMED_SPEAKER_BONUS

    return round(score, 6)


def _normalize_text(text: str) -> str:
    return NORMALIZED_TEXT_PATTERN.sub(" ", text.casefold()).strip()
