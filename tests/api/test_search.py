from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ambient_memory.api.app import create_app
from ambient_memory.api.search import SearchService
from ambient_memory.models import AudioChunk, CanonicalUtterance, Source, TranscriptCandidate, UtteranceSource


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(TSVECTOR, "sqlite")
def _compile_tsvector_sqlite(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_presigned_url(self, client_method: str, *, Params: dict[str, str], ExpiresIn: int) -> str:
        self.calls.append(
            {
                "client_method": client_method,
                "params": Params,
                "expires_in": ExpiresIn,
            }
        )
        return f"https://signed.example/{Params['Bucket']}/{Params['Key']}?expires_in={ExpiresIn}"


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Source.__table__.create(bind=engine)
    AudioChunk.__table__.create(bind=engine)
    TranscriptCandidate.__table__.create(bind=engine)
    CanonicalUtterance.__table__.create(bind=engine)
    UtteranceSource.__table__.create(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def s3_client() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def client(session_factory: sessionmaker[Session], s3_client: FakeS3Client) -> TestClient:
    _seed_search_rows(session_factory)
    app = create_app(
        session_factory=session_factory,
        s3_client=s3_client,
        s3_bucket="ambient-memory",
        presign_expires_in=900,
    )
    return TestClient(app)


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_search_returns_matching_canonical_utterances(client: TestClient) -> None:
    response = client.get("/search", params={"q": "roadmap"})

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "utt-roadmap",
                "text": "Where did we put the roadmap?",
                "started_at": "2026-04-02T13:00:01Z",
                "ended_at": "2026-04-02T13:00:04Z",
                "speaker_name": "Dylan",
                "speaker_confidence": 0.92,
                "canonical_source_id": "desk-a",
                "processing_version": "v1",
                "provenance_summary": {
                    "candidate_count": 2,
                    "chunk_count": 2,
                    "source_ids": ["desk-a", "room-1"],
                },
            }
        ]
    }


def test_search_supports_speaker_and_time_filters(client: TestClient) -> None:
    response = client.get(
        "/search",
        params={
            "speaker": "Alice",
            "from": "2026-04-02T13:59:00Z",
            "to": "2026-04-02T14:05:00Z",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == ["utt-beta"]
    assert body["items"][0]["speaker_name"] == "Alice"


def test_get_utterance_returns_detail_with_replay_audio_links(
    client: TestClient,
    s3_client: FakeS3Client,
) -> None:
    response = client.get("/utterances/utt-roadmap")

    assert response.status_code == 200
    assert response.json() == {
        "id": "utt-roadmap",
        "text": "Where did we put the roadmap?",
        "started_at": "2026-04-02T13:00:01Z",
        "ended_at": "2026-04-02T13:00:04Z",
        "speaker_name": "Dylan",
        "speaker_confidence": 0.92,
        "canonical_source_id": "desk-a",
        "processing_version": "v1",
        "provenance_summary": {
            "candidate_count": 2,
            "chunk_count": 2,
            "source_ids": ["desk-a", "room-1"],
        },
        "provenance": [
            {
                "transcript_candidate_id": "candidate-local-roadmap",
                "audio_chunk_id": "chunk-local-roadmap",
                "source_id": "desk-a",
                "vendor": "deepgram",
                "vendor_segment_id": "utt-local-roadmap",
                "text": "Where did we put the roadmap?",
                "started_at": "2026-04-02T13:00:01Z",
                "ended_at": "2026-04-02T13:00:04Z",
                "is_canonical": True,
            },
            {
                "transcript_candidate_id": "candidate-room-roadmap",
                "audio_chunk_id": "chunk-room-roadmap",
                "source_id": "room-1",
                "vendor": "deepgram",
                "vendor_segment_id": "utt-room-roadmap",
                "text": "where did we put the roadmap",
                "started_at": "2026-04-02T13:00:01.500000Z",
                "ended_at": "2026-04-02T13:00:04.500000Z",
                "is_canonical": False,
            },
        ],
        "replay_audio": [
            {
                "audio_chunk_id": "chunk-local-roadmap",
                "source_id": "desk-a",
                "started_at": "2026-04-02T13:00:00Z",
                "ended_at": "2026-04-02T13:00:30Z",
                "url": "https://signed.example/ambient-memory/raw-audio/desk-a/chunk-local-roadmap.wav?expires_in=900",
            },
            {
                "audio_chunk_id": "chunk-room-roadmap",
                "source_id": "room-1",
                "started_at": "2026-04-02T13:00:00Z",
                "ended_at": "2026-04-02T13:00:30Z",
                "url": "https://signed.example/ambient-memory/raw-audio/room-1/chunk-room-roadmap.wav?expires_in=900",
            },
        ],
    }
    assert s3_client.calls == [
        {
            "client_method": "get_object",
            "params": {
                "Bucket": "ambient-memory",
                "Key": "raw-audio/desk-a/chunk-local-roadmap.wav",
            },
            "expires_in": 900,
        },
        {
            "client_method": "get_object",
            "params": {
                "Bucket": "ambient-memory",
                "Key": "raw-audio/room-1/chunk-room-roadmap.wav",
            },
            "expires_in": 900,
        },
    ]


def test_postgres_search_uses_search_vector_column_when_querying() -> None:
    service = SearchService(
        session_factory=sessionmaker(),
        s3_client=FakeS3Client(),
    )
    postgres_session = SimpleNamespace(
        bind=SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql"),
        )
    )

    stmt = service._apply_filters(
        postgres_session,
        stmt=select(CanonicalUtterance),
        query_text="roadmap plan",
        speaker=None,
        from_at=None,
        to_at=None,
    )
    compiled_stmt = stmt.compile(dialect=postgresql.dialect())
    compiled = str(compiled_stmt).lower()

    assert "canonical_utterances.search_vector" in compiled
    assert "coalesce(canonical_utterances.search_vector, to_tsvector" in compiled
    assert "websearch_to_tsquery" in compiled
    assert "roadmap plan" in compiled_stmt.params.values()


def _seed_search_rows(session_factory: sessionmaker[Session]) -> None:
    session = session_factory()
    try:
        session.add_all(
            [
                Source(id="desk-a", source_type="macbook", device_owner="Dylan"),
                Source(id="desk-b", source_type="macbook", device_owner="Alice"),
                Source(id="room-1", source_type="room", device_owner=None),
                AudioChunk(
                    id="chunk-local-roadmap",
                    source_id="desk-a",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/desk-a/chunk-local-roadmap.wav",
                    status="processed",
                    started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
                ),
                AudioChunk(
                    id="chunk-room-roadmap",
                    source_id="room-1",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-1/chunk-room-roadmap.wav",
                    status="processed",
                    started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
                ),
                AudioChunk(
                    id="chunk-beta",
                    source_id="desk-b",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/desk-b/chunk-beta.wav",
                    status="processed",
                    started_at=datetime(2026, 4, 2, 14, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 14, 0, 30, tzinfo=UTC),
                ),
                TranscriptCandidate(
                    id="candidate-local-roadmap",
                    audio_chunk_id="chunk-local-roadmap",
                    source_id="desk-a",
                    vendor="deepgram",
                    vendor_segment_id="utt-local-roadmap",
                    text="Where did we put the roadmap?",
                    speaker_hint="speaker_0",
                    speaker_confidence=0.92,
                    confidence=0.96,
                    started_at=datetime(2026, 4, 2, 13, 0, 1, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 4, tzinfo=UTC),
                    raw_payload={"segment": "local-roadmap"},
                ),
                TranscriptCandidate(
                    id="candidate-room-roadmap",
                    audio_chunk_id="chunk-room-roadmap",
                    source_id="room-1",
                    vendor="deepgram",
                    vendor_segment_id="utt-room-roadmap",
                    text="where did we put the roadmap",
                    speaker_hint="speaker_0",
                    speaker_confidence=0.78,
                    confidence=0.82,
                    started_at=datetime(2026, 4, 2, 13, 0, 1, 500000, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 4, 500000, tzinfo=UTC),
                    raw_payload={"segment": "room-roadmap"},
                ),
                TranscriptCandidate(
                    id="candidate-beta",
                    audio_chunk_id="chunk-beta",
                    source_id="desk-b",
                    vendor="deepgram",
                    vendor_segment_id="utt-beta",
                    text="Ship the beta tomorrow.",
                    speaker_hint="speaker_1",
                    speaker_confidence=0.89,
                    confidence=0.9,
                    started_at=datetime(2026, 4, 2, 14, 0, 2, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 14, 0, 5, tzinfo=UTC),
                    raw_payload={"segment": "beta"},
                ),
                CanonicalUtterance(
                    id="utt-roadmap",
                    text="Where did we put the roadmap?",
                    started_at=datetime(2026, 4, 2, 13, 0, 1, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 4, tzinfo=UTC),
                    speaker_name="Dylan",
                    speaker_confidence=0.92,
                    canonical_source_id="desk-a",
                    processing_version="v1",
                ),
                CanonicalUtterance(
                    id="utt-beta",
                    text="Ship the beta tomorrow.",
                    started_at=datetime(2026, 4, 2, 14, 0, 2, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 14, 0, 5, tzinfo=UTC),
                    speaker_name="Alice",
                    speaker_confidence=0.89,
                    canonical_source_id="desk-b",
                    processing_version="v1",
                ),
                UtteranceSource(
                    canonical_utterance_id="utt-roadmap",
                    transcript_candidate_id="candidate-local-roadmap",
                    is_canonical=True,
                ),
                UtteranceSource(
                    canonical_utterance_id="utt-roadmap",
                    transcript_candidate_id="candidate-room-roadmap",
                    is_canonical=False,
                ),
                UtteranceSource(
                    canonical_utterance_id="utt-beta",
                    transcript_candidate_id="candidate-beta",
                    is_canonical=True,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()
