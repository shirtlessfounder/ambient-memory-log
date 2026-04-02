from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.integrations.s3_store import presign_chunk_url
from ambient_memory.models import AudioChunk, CanonicalUtterance, TranscriptCandidate, UtteranceSource


@dataclass(frozen=True, slots=True)
class ProvenanceSummaryRecord:
    candidate_count: int
    chunk_count: int
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchUtteranceRecord:
    id: str
    text: str
    started_at: datetime
    ended_at: datetime
    speaker_name: str | None
    speaker_confidence: float | None
    canonical_source_id: str | None
    processing_version: str
    provenance_summary: ProvenanceSummaryRecord


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    transcript_candidate_id: str
    audio_chunk_id: str
    source_id: str
    vendor: str
    vendor_segment_id: str | None
    text: str
    started_at: datetime
    ended_at: datetime
    is_canonical: bool


@dataclass(frozen=True, slots=True)
class ReplayAudioRecord:
    audio_chunk_id: str
    source_id: str
    started_at: datetime
    ended_at: datetime
    url: str


@dataclass(frozen=True, slots=True)
class UtteranceDetailRecord:
    id: str
    text: str
    started_at: datetime
    ended_at: datetime
    speaker_name: str | None
    speaker_confidence: float | None
    canonical_source_id: str | None
    processing_version: str
    provenance_summary: ProvenanceSummaryRecord
    provenance: tuple[ProvenanceRecord, ...]
    replay_audio: tuple[ReplayAudioRecord, ...]


@dataclass(frozen=True, slots=True)
class _JoinedProvenanceRow:
    canonical_utterance_id: str
    transcript_candidate_id: str
    audio_chunk_id: str
    source_id: str
    vendor: str
    vendor_segment_id: str | None
    text: str
    started_at: datetime
    ended_at: datetime
    is_canonical: bool
    chunk_started_at: datetime
    chunk_ended_at: datetime
    s3_bucket: str
    s3_key: str


class SearchService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        s3_client: Any,
        presign_expires_in: int = 3600,
        s3_bucket: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.s3_client = s3_client
        self.presign_expires_in = presign_expires_in
        self.s3_bucket = s3_bucket

    def search_utterances(
        self,
        *,
        query_text: str | None = None,
        speaker: str | None = None,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
    ) -> list[SearchUtteranceRecord]:
        session = self.session_factory()
        try:
            stmt: Select[tuple[CanonicalUtterance]] = select(CanonicalUtterance)
            stmt = self._apply_filters(
                session,
                stmt=stmt,
                query_text=query_text,
                speaker=speaker,
                from_at=from_at,
                to_at=to_at,
            )
            stmt = stmt.order_by(CanonicalUtterance.started_at.desc(), CanonicalUtterance.id)
            utterances = session.execute(stmt).scalars().all()
            provenance_by_utterance = self._load_provenance_rows(
                session,
                utterance_ids=[utterance.id for utterance in utterances],
            )
            return [
                SearchUtteranceRecord(
                    id=utterance.id,
                    text=utterance.text,
                    started_at=_normalize_timestamp(utterance.started_at),
                    ended_at=_normalize_timestamp(utterance.ended_at),
                    speaker_name=utterance.speaker_name,
                    speaker_confidence=utterance.speaker_confidence,
                    canonical_source_id=utterance.canonical_source_id,
                    processing_version=utterance.processing_version,
                    provenance_summary=_build_provenance_summary(provenance_by_utterance.get(utterance.id, [])),
                )
                for utterance in utterances
            ]
        finally:
            session.close()

    def get_utterance_detail(self, utterance_id: str) -> UtteranceDetailRecord | None:
        session = self.session_factory()
        try:
            utterance = session.get(CanonicalUtterance, utterance_id)
            if utterance is None:
                return None

            provenance_rows = self._load_provenance_rows(session, utterance_ids=[utterance_id]).get(utterance_id, [])
            return UtteranceDetailRecord(
                id=utterance.id,
                text=utterance.text,
                started_at=_normalize_timestamp(utterance.started_at),
                ended_at=_normalize_timestamp(utterance.ended_at),
                speaker_name=utterance.speaker_name,
                speaker_confidence=utterance.speaker_confidence,
                canonical_source_id=utterance.canonical_source_id,
                processing_version=utterance.processing_version,
                provenance_summary=_build_provenance_summary(provenance_rows),
                provenance=tuple(
                    ProvenanceRecord(
                        transcript_candidate_id=row.transcript_candidate_id,
                        audio_chunk_id=row.audio_chunk_id,
                        source_id=row.source_id,
                        vendor=row.vendor,
                        vendor_segment_id=row.vendor_segment_id,
                        text=row.text,
                        started_at=_normalize_timestamp(row.started_at),
                        ended_at=_normalize_timestamp(row.ended_at),
                        is_canonical=row.is_canonical,
                    )
                    for row in provenance_rows
                ),
                replay_audio=_build_replay_audio(
                    provenance_rows,
                    s3_client=self.s3_client,
                    presign_expires_in=self.presign_expires_in,
                    fallback_bucket=self.s3_bucket,
                ),
            )
        finally:
            session.close()

    def _apply_filters(
        self,
        session: Session,
        *,
        stmt: Select[tuple[CanonicalUtterance]],
        query_text: str | None,
        speaker: str | None,
        from_at: datetime | None,
        to_at: datetime | None,
    ) -> Select[tuple[CanonicalUtterance]]:
        normalized_query = (query_text or "").strip()
        if normalized_query:
            dialect_name = session.bind.dialect.name if session.bind is not None else ""
            if dialect_name == "postgresql":
                search_vector = func.to_tsvector("english", CanonicalUtterance.text)
                ts_query = func.websearch_to_tsquery("english", normalized_query)
                stmt = stmt.where(search_vector.op("@@")(ts_query))
            else:
                for term in normalized_query.split():
                    stmt = stmt.where(CanonicalUtterance.text.ilike(f"%{term}%"))

        if speaker:
            stmt = stmt.where(CanonicalUtterance.speaker_name == speaker)
        if from_at is not None:
            stmt = stmt.where(CanonicalUtterance.ended_at >= from_at)
        if to_at is not None:
            stmt = stmt.where(CanonicalUtterance.started_at <= to_at)

        return stmt

    def _load_provenance_rows(
        self,
        session: Session,
        *,
        utterance_ids: list[str],
    ) -> dict[str, list[_JoinedProvenanceRow]]:
        if not utterance_ids:
            return {}

        rows = session.execute(
            select(
                UtteranceSource.canonical_utterance_id.label("canonical_utterance_id"),
                TranscriptCandidate.id.label("transcript_candidate_id"),
                AudioChunk.id.label("audio_chunk_id"),
                TranscriptCandidate.source_id.label("source_id"),
                TranscriptCandidate.vendor.label("vendor"),
                TranscriptCandidate.vendor_segment_id.label("vendor_segment_id"),
                TranscriptCandidate.text.label("text"),
                TranscriptCandidate.started_at.label("started_at"),
                TranscriptCandidate.ended_at.label("ended_at"),
                UtteranceSource.is_canonical.label("is_canonical"),
                AudioChunk.started_at.label("chunk_started_at"),
                AudioChunk.ended_at.label("chunk_ended_at"),
                AudioChunk.s3_bucket.label("s3_bucket"),
                AudioChunk.s3_key.label("s3_key"),
            )
            .join(
                TranscriptCandidate,
                TranscriptCandidate.id == UtteranceSource.transcript_candidate_id,
            )
            .join(AudioChunk, AudioChunk.id == TranscriptCandidate.audio_chunk_id)
            .where(UtteranceSource.canonical_utterance_id.in_(utterance_ids))
            .order_by(
                UtteranceSource.canonical_utterance_id,
                TranscriptCandidate.started_at,
                TranscriptCandidate.source_id,
                TranscriptCandidate.id,
            )
        ).all()

        grouped: dict[str, list[_JoinedProvenanceRow]] = defaultdict(list)
        for row in rows:
            grouped[row.canonical_utterance_id].append(
                _JoinedProvenanceRow(
                    canonical_utterance_id=row.canonical_utterance_id,
                    transcript_candidate_id=row.transcript_candidate_id,
                    audio_chunk_id=row.audio_chunk_id,
                    source_id=row.source_id,
                    vendor=row.vendor,
                    vendor_segment_id=row.vendor_segment_id,
                    text=row.text,
                    started_at=row.started_at,
                    ended_at=row.ended_at,
                    is_canonical=row.is_canonical,
                    chunk_started_at=row.chunk_started_at,
                    chunk_ended_at=row.chunk_ended_at,
                    s3_bucket=row.s3_bucket,
                    s3_key=row.s3_key,
                )
            )
        return dict(grouped)


def _build_provenance_summary(rows: list[_JoinedProvenanceRow]) -> ProvenanceSummaryRecord:
    return ProvenanceSummaryRecord(
        candidate_count=len(rows),
        chunk_count=len({row.audio_chunk_id for row in rows}),
        source_ids=tuple(sorted({row.source_id for row in rows})),
    )


def _build_replay_audio(
    rows: list[_JoinedProvenanceRow],
    *,
    s3_client: Any,
    presign_expires_in: int,
    fallback_bucket: str | None,
) -> tuple[ReplayAudioRecord, ...]:
    deduped_rows: dict[str, _JoinedProvenanceRow] = {}
    for row in rows:
        deduped_rows.setdefault(row.audio_chunk_id, row)

    return tuple(
        ReplayAudioRecord(
            audio_chunk_id=row.audio_chunk_id,
            source_id=row.source_id,
            started_at=_normalize_timestamp(row.chunk_started_at),
            ended_at=_normalize_timestamp(row.chunk_ended_at),
            url=presign_chunk_url(
                client=s3_client,
                bucket=row.s3_bucket or fallback_bucket or "",
                key=row.s3_key,
                expires_in=presign_expires_in,
            ),
        )
        for row in sorted(
            deduped_rows.values(),
            key=lambda item: (item.chunk_started_at, item.source_id, item.audio_chunk_id),
        )
    )


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
