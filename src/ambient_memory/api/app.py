from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

import boto3
from fastapi import FastAPI, HTTPException, Query
from sqlalchemy.orm import Session, sessionmaker
import uvicorn

from ambient_memory.api.schemas import (
    HealthResponse,
    ProvenanceResponse,
    ProvenanceSummaryResponse,
    ReplayAudioResponse,
    SearchResponse,
    SearchUtteranceResponse,
    UtteranceDetailResponse,
)
from ambient_memory.api.search import SearchService, SearchUtteranceRecord, UtteranceDetailRecord
from ambient_memory.config import ApiSettings, load_settings
from ambient_memory.db import build_session_factory


def create_app(
    *,
    session_factory: sessionmaker[Session],
    s3_client: Any,
    s3_bucket: str | None = None,
    presign_expires_in: int = 3600,
) -> FastAPI:
    service = SearchService(
        session_factory=session_factory,
        s3_client=s3_client,
        s3_bucket=s3_bucket,
        presign_expires_in=presign_expires_in,
    )
    app = FastAPI(title="Ambient Memory API")

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/search", response_model=SearchResponse)
    def search(
        q: str | None = Query(default=None),
        speaker: str | None = Query(default=None),
        from_at: Annotated[datetime | None, Query(alias="from")] = None,
        to_at: Annotated[datetime | None, Query(alias="to")] = None,
    ) -> SearchResponse:
        return SearchResponse(
            items=[
                _serialize_search_item(item)
                for item in service.search_utterances(
                    query_text=q,
                    speaker=speaker,
                    from_at=from_at,
                    to_at=to_at,
                )
            ]
        )

    @app.get("/utterances/{utterance_id}", response_model=UtteranceDetailResponse)
    def get_utterance(utterance_id: str) -> UtteranceDetailResponse:
        detail = service.get_utterance_detail(utterance_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Utterance not found")
        return _serialize_utterance_detail(detail)

    return app


def run_api_server(
    *,
    host: str | None = None,
    port: int | None = None,
    env_file: str | None = None,
    settings: ApiSettings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    s3_client: Any | None = None,
    server_runner: Callable[..., Any] = uvicorn.run,
) -> None:
    resolved_settings = settings or load_settings(ApiSettings, env_file=env_file)
    resolved_session_factory = session_factory or build_session_factory(resolved_settings)
    resolved_s3_client = s3_client or boto3.client("s3", region_name=resolved_settings.aws_region)

    app = create_app(
        session_factory=resolved_session_factory,
        s3_client=resolved_s3_client,
        presign_expires_in=resolved_settings.api_presign_expires_in,
    )
    server_runner(
        app,
        host=host or resolved_settings.api_host,
        port=port or resolved_settings.api_port,
    )


def _serialize_search_item(item: SearchUtteranceRecord) -> SearchUtteranceResponse:
    return SearchUtteranceResponse(
        id=item.id,
        text=item.text,
        started_at=item.started_at,
        ended_at=item.ended_at,
        speaker_name=item.speaker_name,
        speaker_confidence=item.speaker_confidence,
        canonical_source_id=item.canonical_source_id,
        processing_version=item.processing_version,
        provenance_summary=ProvenanceSummaryResponse(
            candidate_count=item.provenance_summary.candidate_count,
            chunk_count=item.provenance_summary.chunk_count,
            source_ids=list(item.provenance_summary.source_ids),
        ),
    )


def _serialize_utterance_detail(detail: UtteranceDetailRecord) -> UtteranceDetailResponse:
    return UtteranceDetailResponse(
        id=detail.id,
        text=detail.text,
        started_at=detail.started_at,
        ended_at=detail.ended_at,
        speaker_name=detail.speaker_name,
        speaker_confidence=detail.speaker_confidence,
        canonical_source_id=detail.canonical_source_id,
        processing_version=detail.processing_version,
        provenance_summary=ProvenanceSummaryResponse(
            candidate_count=detail.provenance_summary.candidate_count,
            chunk_count=detail.provenance_summary.chunk_count,
            source_ids=list(detail.provenance_summary.source_ids),
        ),
        provenance=[
            ProvenanceResponse(
                transcript_candidate_id=row.transcript_candidate_id,
                audio_chunk_id=row.audio_chunk_id,
                source_id=row.source_id,
                vendor=row.vendor,
                vendor_segment_id=row.vendor_segment_id,
                text=row.text,
                started_at=row.started_at,
                ended_at=row.ended_at,
                is_canonical=row.is_canonical,
            )
            for row in detail.provenance
        ],
        replay_audio=[
            ReplayAudioResponse(
                audio_chunk_id=row.audio_chunk_id,
                source_id=row.source_id,
                started_at=row.started_at,
                ended_at=row.ended_at,
                url=row.url,
            )
            for row in detail.replay_audio
        ],
    )
