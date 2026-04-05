from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "aa_sources"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    device_owner: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    audio_chunks: Mapped[list["AudioChunk"]] = relationship(back_populates="source")


class AudioChunk(Base):
    __tablename__ = "aa_audio_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(ForeignKey("aa_sources.id"), nullable=False)
    s3_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(50), default="uploaded", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    source: Mapped["Source"] = relationship(back_populates="audio_chunks")
    transcript_candidates: Mapped[list["TranscriptCandidate"]] = relationship(back_populates="audio_chunk")


class Voiceprint(Base):
    __tablename__ = "aa_voiceprints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    speaker_label: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), default="pyannote", nullable=False)
    provider_voiceprint_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_audio_key: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TranscriptCandidate(Base):
    __tablename__ = "aa_transcript_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    audio_chunk_id: Mapped[str] = mapped_column(ForeignKey("aa_audio_chunks.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("aa_sources.id"), nullable=False)
    vendor: Mapped[str] = mapped_column(String(50), nullable=False)
    vendor_segment_id: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker_hint: Mapped[str | None] = mapped_column(String(100))
    speaker_confidence: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    audio_chunk: Mapped["AudioChunk"] = relationship(back_populates="transcript_candidates")


class CanonicalUtterance(Base):
    __tablename__ = "aa_canonical_utterances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    speaker_name: Mapped[str | None] = mapped_column(String(100))
    speaker_confidence: Mapped[float | None] = mapped_column(Float)
    canonical_source_id: Mapped[str | None] = mapped_column(ForeignKey("aa_sources.id"))
    processing_version: Mapped[str] = mapped_column(String(50), default="v1", nullable=False)
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    provenance: Mapped[list["UtteranceSource"]] = relationship(back_populates="canonical_utterance")


class UtteranceSource(Base):
    __tablename__ = "aa_utterance_sources"

    canonical_utterance_id: Mapped[str] = mapped_column(
        ForeignKey("aa_canonical_utterances.id"),
        primary_key=True,
    )
    transcript_candidate_id: Mapped[str] = mapped_column(
        ForeignKey("aa_transcript_candidates.id"),
        primary_key=True,
    )
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    canonical_utterance: Mapped["CanonicalUtterance"] = relationship(back_populates="provenance")


class AgentHeartbeat(Base):
    __tablename__ = "aa_agent_heartbeats"

    source_id: Mapped[str] = mapped_column(ForeignKey("aa_sources.id"), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_upload_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


Index("ix_audio_chunks_status_started_at", AudioChunk.status, AudioChunk.started_at)
Index("ix_canonical_utterances_started_at", CanonicalUtterance.started_at)
Index(
    "ix_canonical_utterances_search_vector",
    CanonicalUtterance.search_vector,
    postgresql_using="gin",
)
