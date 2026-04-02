from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class ProvenanceSummaryResponse(BaseModel):
    candidate_count: int
    chunk_count: int
    source_ids: list[str]


class SearchUtteranceResponse(BaseModel):
    id: str
    text: str
    started_at: datetime
    ended_at: datetime
    speaker_name: str | None
    speaker_confidence: float | None
    canonical_source_id: str | None
    processing_version: str
    provenance_summary: ProvenanceSummaryResponse


class SearchResponse(BaseModel):
    items: list[SearchUtteranceResponse]


class ProvenanceResponse(BaseModel):
    transcript_candidate_id: str
    audio_chunk_id: str
    source_id: str
    vendor: str
    vendor_segment_id: str | None
    text: str
    started_at: datetime
    ended_at: datetime
    is_canonical: bool


class ReplayAudioResponse(BaseModel):
    audio_chunk_id: str
    source_id: str
    started_at: datetime
    ended_at: datetime
    url: str


class UtteranceDetailResponse(BaseModel):
    id: str
    text: str
    started_at: datetime
    ended_at: datetime
    speaker_name: str | None
    speaker_confidence: float | None
    canonical_source_id: str | None
    processing_version: str
    provenance_summary: ProvenanceSummaryResponse
    provenance: list[ProvenanceResponse]
    replay_audio: list[ReplayAudioResponse]
