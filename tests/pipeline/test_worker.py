from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.integrations.pyannote_client import IdentificationMatch
from ambient_memory.models import AudioChunk, CanonicalUtterance, Source, TranscriptCandidate, UtteranceSource, Voiceprint
from ambient_memory.pipeline.worker import PipelineWorker


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(TSVECTOR, "sqlite")
def _compile_tsvector_sqlite(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


class FakeS3Client:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.calls: list[dict[str, str]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, BytesIO]:
        self.calls.append({"Bucket": Bucket, "Key": Key})
        return {"Body": BytesIO(self.objects[Key])}


class FakeDeepgramClient:
    def __init__(self, payloads: dict[bytes, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: list[bytes] = []

    def transcribe_bytes(self, audio_bytes: bytes, *, content_type: str = "audio/wav") -> dict[str, Any]:
        assert content_type == "audio/wav"
        self.calls.append(audio_bytes)
        return self.payloads[audio_bytes]


class FakePyannoteClient:
    def __init__(self, matches: dict[bytes, list[IdentificationMatch]]) -> None:
        self.matches = matches
        self.calls: list[dict[str, Any]] = []

    def identify_speakers(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        voiceprints: list[Any],
        matching_threshold: int = 0,
        exclusive: bool = True,
    ) -> list[IdentificationMatch]:
        self.calls.append(
            {
                "audio_bytes": audio_bytes,
                "filename": filename,
                "voiceprints": voiceprints,
                "matching_threshold": matching_threshold,
                "exclusive": exclusive,
            }
        )
        return self.matches[audio_bytes]


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Source.__table__.create(bind=engine)
    AudioChunk.__table__.create(bind=engine)
    Voiceprint.__table__.create(bind=engine)
    TranscriptCandidate.__table__.create(bind=engine)
    CanonicalUtterance.__table__.create(bind=engine)
    UtteranceSource.__table__.create(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_pending_window(session_factory: sessionmaker[Session]) -> None:
    session = session_factory()
    try:
        session.add_all(
            [
                Source(id="desk-a", source_type="macbook", device_owner="Dylan"),
                Source(id="room-1", source_type="room", device_owner=None),
                Voiceprint(
                    speaker_label="Dylan",
                    provider="pyannote",
                    provider_voiceprint_id="vp-1",
                    source_audio_key="voiceprints/dylan.wav",
                ),
                AudioChunk(
                    id="chunk-local",
                    source_id="desk-a",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/desk-a/chunk-local.wav",
                    status="uploaded",
                    started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
                ),
                AudioChunk(
                    id="chunk-room",
                    source_id="room-1",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-1/chunk-room.wav",
                    status="uploaded",
                    started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()


def _seed_single_uploaded_chunk(session_factory: sessionmaker[Session]) -> None:
    session = session_factory()
    try:
        session.add(Source(id="desk-a", source_type="macbook", device_owner="Dylan"))
        session.add(
            AudioChunk(
                id="chunk-local",
                source_id="desk-a",
                s3_bucket="ambient-memory",
                s3_key="raw-audio/desk-a/chunk-local.wav",
                status="uploaded",
                started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
                ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
            )
        )
        session.commit()
    finally:
        session.close()


def test_pipeline_worker_processes_one_window_and_persists_canonical_utterance(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_pending_window(session_factory)
    local_audio = b"local-audio"
    room_audio = b"room-audio"
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client(
            {
                "raw-audio/desk-a/chunk-local.wav": local_audio,
                "raw-audio/room-1/chunk-room.wav": room_audio,
            }
        ),
        deepgram_client=FakeDeepgramClient(
            {
                local_audio: {
                    "results": {
                        "utterances": [
                            {
                                "id": "utt-local",
                                "start": 1.0,
                                "end": 3.0,
                                "confidence": 0.94,
                                "speaker": 0,
                                "speaker_confidence": 0.82,
                                "transcript": "Hello there.",
                            }
                        ]
                    }
                },
                room_audio: {
                    "results": {
                        "utterances": [
                            {
                                "id": "utt-room",
                                "start": 1.2,
                                "end": 3.2,
                                "confidence": 0.84,
                                "speaker": 0,
                                "speaker_confidence": 0.74,
                                "transcript": "hello there",
                            }
                        ]
                    }
                },
            }
        ),
        pyannote_client=FakePyannoteClient(
            {
                local_audio: [
                    IdentificationMatch(
                        speaker="speaker_0",
                        match="Dylan",
                        confidence={"Dylan": 0.82},
                    )
                ],
                room_audio: [
                    IdentificationMatch(
                        speaker="speaker_0",
                        match="Dylan",
                        confidence={"Dylan": 0.74},
                    )
                ],
            }
        ),
    )

    result = worker.run_once()

    session = session_factory()
    try:
        chunks = session.scalars(select(AudioChunk).order_by(AudioChunk.source_id)).all()
        candidates = session.scalars(select(TranscriptCandidate).order_by(TranscriptCandidate.source_id)).all()
        canonical = session.scalars(select(CanonicalUtterance)).all()
        provenance = session.scalars(select(UtteranceSource).order_by(UtteranceSource.transcript_candidate_id)).all()
    finally:
        session.close()

    assert result.pending_chunks == 2
    assert result.windows == 1
    assert result.processed_chunks == 2
    assert result.failed_chunks == 0
    assert [chunk.status for chunk in chunks] == ["processed", "processed"]
    assert [candidate.text for candidate in candidates] == ["Hello there.", "hello there"]
    assert len(canonical) == 1
    assert canonical[0].canonical_source_id == "desk-a"
    assert canonical[0].text == "Hello there."
    assert canonical[0].speaker_name == "Dylan"
    assert len(provenance) == 2
    assert sum(row.is_canonical for row in provenance) == 1


def test_pipeline_worker_run_once_dry_run_reports_pending_chunks_without_mutation(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_single_uploaded_chunk(session_factory)
    s3_client = FakeS3Client({"raw-audio/desk-a/chunk-local.wav": b"local-audio"})
    deepgram_client = FakeDeepgramClient({b"local-audio": {"results": {"utterances": []}}})
    pyannote_client = FakePyannoteClient({b"local-audio": []})
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=s3_client,
        deepgram_client=deepgram_client,
        pyannote_client=pyannote_client,
    )

    result = worker.run_once(dry_run=True)

    session = session_factory()
    try:
        chunk = session.get(AudioChunk, "chunk-local")
        candidates = session.scalars(select(TranscriptCandidate)).all()
        canonical = session.scalars(select(CanonicalUtterance)).all()
    finally:
        session.close()

    assert result.dry_run is True
    assert result.pending_chunks == 1
    assert result.windows == 1
    assert result.processed_chunks == 0
    assert result.failed_chunks == 0
    assert chunk is not None
    assert chunk.status == "uploaded"
    assert candidates == []
    assert canonical == []
    assert deepgram_client.calls == []
    assert pyannote_client.calls == []
    assert s3_client.calls == []
