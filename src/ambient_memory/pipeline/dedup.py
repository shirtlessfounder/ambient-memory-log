from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Iterable

from sqlalchemy.orm import Session

from ambient_memory.models import CanonicalUtterance, UtteranceSource


LOCAL_SOURCE_BONUS = 0.15
NAMED_SPEAKER_BONUS = 0.05
DEDUP_TOLERANCE_SECONDS = 5.0
LOW_SIGNAL_EXACT_MATCH_TOLERANCE_SECONDS = 1.5
MIN_FUZZY_INFORMATIVE_TOKENS = 3
INFORMATIVE_TOKEN_OVERLAP_THRESHOLD = 0.8
NORMALIZED_TEXT_PATTERN = re.compile(r"[^a-z0-9]+")
APOSTROPHE_PATTERN = re.compile(r"[’']")
NEGATION_TOKENS = frozenset({"cannot", "never", "no", "nor", "not", "without"})
NON_INFORMATIVE_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "be",
        "for",
        "i",
        "if",
        "in",
        "is",
        "it",
        "just",
        "me",
        "of",
        "ok",
        "okay",
        "or",
        "right",
        "so",
        "that",
        "the",
        "there",
        "these",
        "this",
        "those",
        "to",
        "uh",
        "uhh",
        "um",
        "umm",
        "we",
        "well",
        "yeah",
        "yep",
        "yes",
        "you",
    }
)


@dataclass(frozen=True, slots=True)
class _TextSignature:
    normalized_text: str
    normalized_tokens: tuple[str, ...]
    informative_tokens: tuple[str, ...]
    is_low_signal: bool


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
            raw_speaker_name=utterance.speaker_name,
            raw_speaker_confidence=utterance.speaker_confidence,
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
    low_signal_exact_tolerance = timedelta(seconds=LOW_SIGNAL_EXACT_MATCH_TOLERANCE_SECONDS)
    candidate_signature = _build_text_signature(candidate.text)
    if not _group_speakers_are_compatible(candidate, group):
        return False

    for existing in group:
        if not _times_overlap(candidate, existing, tolerance=tolerance):
            continue
        if _named_speakers_conflict(candidate, existing):
            continue
        existing_signature = _build_text_signature(existing.text)
        if existing_signature.normalized_text == candidate_signature.normalized_text:
            if candidate.source_id == existing.source_id:
                continue
            exact_tolerance = (
                low_signal_exact_tolerance
                if candidate_signature.is_low_signal or existing_signature.is_low_signal
                else tolerance
            )
            if _times_overlap(candidate, existing, tolerance=exact_tolerance):
                return True
            continue
        if _fuzzy_matches(candidate, candidate_signature, existing, existing_signature):
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
    normalized = APOSTROPHE_PATTERN.sub("'", text.casefold())
    normalized = normalized.replace("can't", "cannot")
    normalized = normalized.replace("won't", "will not")
    normalized = normalized.replace("shan't", "shall not")
    normalized = normalized.replace("ain't", "is not")
    normalized = re.sub(r"n't\b", " not", normalized)
    return NORMALIZED_TEXT_PATTERN.sub(" ", normalized).strip()


def _build_text_signature(text: str) -> _TextSignature:
    normalized_tokens = _normalize_tokens(text)
    informative_tokens = _informative_tokens(normalized_tokens)
    return _TextSignature(
        normalized_text=" ".join(normalized_tokens),
        normalized_tokens=normalized_tokens,
        informative_tokens=informative_tokens,
        is_low_signal=_is_low_signal(normalized_tokens, informative_tokens),
    )


def _normalize_tokens(text: str) -> tuple[str, ...]:
    normalized_text = _normalize_text(text)
    if not normalized_text:
        return ()
    return tuple(normalized_text.split())


def _informative_tokens(tokens: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        token
        for token in tokens
        if len(token) > 1 and token not in NON_INFORMATIVE_TOKENS
    )


def _is_low_signal(normalized_tokens: tuple[str, ...], informative_tokens: tuple[str, ...]) -> bool:
    return len(informative_tokens) < 2 or len(normalized_tokens) <= 2


def _times_overlap(
    candidate: DedupCandidate,
    existing: DedupCandidate,
    *,
    tolerance: timedelta,
) -> bool:
    return candidate.started_at <= existing.ended_at + tolerance and candidate.ended_at >= existing.started_at - tolerance


def _fuzzy_matches(
    candidate: DedupCandidate,
    candidate_signature: _TextSignature,
    existing: DedupCandidate,
    existing_signature: _TextSignature,
) -> bool:
    candidate_token_set = set(candidate_signature.informative_tokens)
    existing_token_set = set(existing_signature.informative_tokens)
    if candidate.source_id == existing.source_id:
        return False
    if candidate_signature.is_low_signal or existing_signature.is_low_signal:
        return False
    if _named_speakers_conflict(candidate, existing):
        return False
    if _has_unshared_negation(candidate_token_set, existing_token_set):
        return False
    if _has_meaningful_containment(candidate_signature, existing_signature):
        return True
    if _has_conflicting_informative_tokens(candidate_token_set, existing_token_set):
        return False
    return _token_overlap_coefficient(
        candidate_signature.informative_tokens,
        existing_signature.informative_tokens,
    ) >= INFORMATIVE_TOKEN_OVERLAP_THRESHOLD


def _named_speakers_conflict(candidate: DedupCandidate, existing: DedupCandidate) -> bool:
    if not candidate.speaker_name or not existing.speaker_name:
        return False
    return _normalize_text(candidate.speaker_name) != _normalize_text(existing.speaker_name)


def _group_speakers_are_compatible(candidate: DedupCandidate, group: list[DedupCandidate]) -> bool:
    named_speakers = {
        _normalize_text(item.speaker_name)
        for item in group
        if item.speaker_name
    }
    if candidate.speaker_name:
        named_speakers.add(_normalize_text(candidate.speaker_name))
    named_speakers.discard("")
    return len(named_speakers) <= 1


def _has_meaningful_containment(candidate_signature: _TextSignature, existing_signature: _TextSignature) -> bool:
    shorter, longer = sorted(
        (candidate_signature, existing_signature),
        key=lambda signature: len(signature.normalized_text),
    )
    if len(shorter.informative_tokens) < MIN_FUZZY_INFORMATIVE_TOKENS:
        return False
    if _has_unshared_negation(set(shorter.informative_tokens), set(longer.informative_tokens)):
        return False
    return f" {shorter.normalized_text} " in f" {longer.normalized_text} "


def _token_overlap_coefficient(
    candidate_tokens: tuple[str, ...],
    existing_tokens: tuple[str, ...],
) -> float:
    candidate_token_set = set(candidate_tokens)
    existing_token_set = set(existing_tokens)
    if (
        min(len(candidate_token_set), len(existing_token_set)) < MIN_FUZZY_INFORMATIVE_TOKENS
        or not candidate_token_set
        or not existing_token_set
    ):
        return 0.0
    return len(candidate_token_set & existing_token_set) / max(len(candidate_token_set), len(existing_token_set))


def _has_unshared_negation(candidate_tokens: set[str], existing_tokens: set[str]) -> bool:
    return bool((candidate_tokens ^ existing_tokens) & NEGATION_TOKENS)


def _has_conflicting_informative_tokens(candidate_tokens: set[str], existing_tokens: set[str]) -> bool:
    return bool(candidate_tokens - existing_tokens) and bool(existing_tokens - candidate_tokens)
