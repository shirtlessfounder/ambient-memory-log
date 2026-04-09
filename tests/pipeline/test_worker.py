from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
import wave

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.integrations.assemblyai_client import (
    AssemblyAIClientError,
    AssemblyAISpeakerProfile,
    AssemblyAIUtterance,
)
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


class FakeAssemblyAIClient:
    def __init__(self, responses: dict[bytes, list[AssemblyAIUtterance] | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def transcribe_bytes(
        self,
        audio_bytes: bytes,
        *,
        speakers: tuple[AssemblyAISpeakerProfile, ...],
    ) -> list[AssemblyAIUtterance]:
        self.calls.append(
            {
                "audio_bytes": audio_bytes,
                "speakers": speakers,
            }
        )
        response = self.responses[audio_bytes]
        if isinstance(response, Exception):
            raise response
        return response


ROOM_SPEAKERS = (
    AssemblyAISpeakerProfile(name="Dylan", aliases=("dylan", "dylan vu")),
    AssemblyAISpeakerProfile(name="Niyant", aliases=("niyant",)),
    AssemblyAISpeakerProfile(name="Alex", aliases=("alex", "alexander janiak")),
    AssemblyAISpeakerProfile(name="Jakub", aliases=("jakub", "jakub janiak")),
)


def _wav_bytes(frames: bytes) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(frames)
    return buffer.getvalue()


def _wav_chunk(sample: int) -> tuple[bytes, bytes]:
    frames = bytes([sample % 256, 0]) * 4
    return _wav_bytes(frames), frames


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


def _seed_room_uploaded_chunks(
    session_factory: sessionmaker[Session],
    *,
    count: int,
    source_id: str = "room-1",
    start_at: datetime = datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
) -> list[tuple[str, str]]:
    session = session_factory()
    try:
        source = session.get(Source, source_id)
        if source is None:
            session.add(Source(id=source_id, source_type="room", device_owner=None))

        chunk_rows: list[AudioChunk] = []
        for index in range(count):
            chunk_id = f"{source_id}-chunk-{index:02d}"
            s3_key = f"raw-audio/{source_id}/chunk-{index:02d}.wav"
            chunk_started_at = start_at + timedelta(seconds=index * 30)
            chunk_rows.append(
                AudioChunk(
                    id=chunk_id,
                    source_id=source_id,
                    s3_bucket="ambient-memory",
                    s3_key=s3_key,
                    status="uploaded",
                    started_at=chunk_started_at,
                    ended_at=chunk_started_at + timedelta(seconds=30),
                )
            )

        session.add_all(chunk_rows)
        session.commit()
        return [(row.id, row.s3_key) for row in chunk_rows]
    finally:
        session.close()


def test_pipeline_worker_room_keeps_short_room_window_pending_and_hidden_until_ready(
    session_factory: sessionmaker[Session],
) -> None:
    room_chunks = _seed_room_uploaded_chunks(session_factory, count=2)
    room_audio_objects = {key: _wav_chunk(index + 1)[0] for index, (_chunk_id, key) in enumerate(room_chunks)}
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client(room_audio_objects),
        deepgram_client=FakeDeepgramClient({}),
        pyannote_client=FakePyannoteClient({}),
        assemblyai_client=FakeAssemblyAIClient({}),
        room_speakers=ROOM_SPEAKERS,
        room_assembly_window_seconds=600,
        room_assembly_idle_flush_seconds=120,
        now=lambda: datetime(2026, 4, 2, 13, 1, 31, tzinfo=UTC),
    )

    result = worker.run_once()

    session = session_factory()
    try:
        chunks = session.scalars(select(AudioChunk).order_by(AudioChunk.started_at)).all()
        candidates = session.scalars(select(TranscriptCandidate)).all()
        canonical = session.scalars(select(CanonicalUtterance)).all()
    finally:
        session.close()

    assert result.pending_chunks == 2
    assert result.windows == 0
    assert result.processed_chunks == 0
    assert result.failed_chunks == 0
    assert [chunk.status for chunk in chunks] == ["uploaded", "uploaded"]
    assert worker.s3_client.calls == []
    assert worker.deepgram_client.calls == []
    assert worker.pyannote_client.calls == []
    assert worker.assemblyai_client.calls == []
    assert candidates == []
    assert canonical == []
    assert all(chunk.error_message is None for chunk in chunks)


def test_pipeline_worker_room_publishes_named_room_batch_when_ready(
    session_factory: sessionmaker[Session],
) -> None:
    room_chunks = _seed_room_uploaded_chunks(session_factory, count=20)
    room_audio_objects: dict[str, bytes] = {}
    room_frames = b""
    for index, (_chunk_id, key) in enumerate(room_chunks):
        wav_bytes, frames = _wav_chunk(index + 1)
        room_audio_objects[key] = wav_bytes
        room_frames += frames
    stitched_room_audio = _wav_bytes(room_frames)
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client(room_audio_objects),
        deepgram_client=FakeDeepgramClient({}),
        pyannote_client=FakePyannoteClient({}),
        assemblyai_client=FakeAssemblyAIClient(
            {
                stitched_room_audio: [
                    AssemblyAIUtterance(
                        vendor_segment_id="utt-room",
                        text="Real room speaker turn.",
                        speaker_hint="A",
                        speaker_name="Dylan",
                        confidence=0.94,
                        start_seconds=45.0,
                        end_seconds=75.0,
                        raw_payload={"speaker": "Dylan"},
                    )
                ]
            }
        ),
        room_speakers=ROOM_SPEAKERS,
        room_assembly_window_seconds=600,
        room_assembly_idle_flush_seconds=120,
        now=lambda: datetime(2026, 4, 2, 13, 10, 5, tzinfo=UTC),
    )

    result = worker.run_once()

    session = session_factory()
    try:
        chunks = session.scalars(select(AudioChunk).order_by(AudioChunk.started_at)).all()
        candidates = session.scalars(select(TranscriptCandidate).order_by(TranscriptCandidate.started_at)).all()
        canonical = session.scalars(select(CanonicalUtterance).order_by(CanonicalUtterance.started_at)).all()
        provenance = session.scalars(select(UtteranceSource).order_by(UtteranceSource.transcript_candidate_id)).all()
    finally:
        session.close()

    assert result.pending_chunks == 20
    assert result.windows == 1
    assert result.processed_chunks == 20
    assert result.failed_chunks == 0
    assert [chunk.status for chunk in chunks] == ["processed"] * 20
    assert [call["Key"] for call in worker.s3_client.calls] == [key for _chunk_id, key in room_chunks]
    assert worker.deepgram_client.calls == []
    assert worker.pyannote_client.calls == []
    assert len(worker.assemblyai_client.calls) == 1
    assert worker.assemblyai_client.calls[0]["audio_bytes"] == stitched_room_audio
    assert [speaker.name for speaker in worker.assemblyai_client.calls[0]["speakers"]] == [
        "Dylan",
        "Niyant",
        "Alex",
        "Jakub",
    ]
    assert len(candidates) == 1
    assert candidates[0].vendor == "assemblyai"
    assert candidates[0].text == "Real room speaker turn."
    assert candidates[0].speaker_hint == "A"
    assert len(canonical) == 1
    assert canonical[0].speaker_name == "Dylan"
    assert canonical[0].canonical_source_id == "room-1"
    assert len(provenance) == 1
    assert provenance[0].is_canonical is True


def test_pipeline_worker_keeps_non_room_1_sources_off_assemblyai_path(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    try:
        session.add_all(
            [
                Source(id="room-2", source_type="room", device_owner="conference-room"),
                Voiceprint(
                    speaker_label="Dylan",
                    provider="pyannote",
                    provider_voiceprint_id="vp-1",
                    source_audio_key="voiceprints/dylan.wav",
                ),
                AudioChunk(
                    id="chunk-room-2",
                    source_id="room-2",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-2/chunk-room-2.wav",
                    status="uploaded",
                    started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    room_audio = b"room-two-audio"
    deepgram_client = FakeDeepgramClient(
        {
            room_audio: {
                "results": {
                    "utterances": [
                        {
                            "id": "utt-room-2",
                            "start": 1.0,
                            "end": 3.0,
                            "confidence": 0.84,
                            "speaker": 0,
                            "speaker_confidence": 0.74,
                            "transcript": "Legacy room path stays here.",
                        }
                    ]
                }
            }
        }
    )
    pyannote_client = FakePyannoteClient(
        {
            room_audio: [
                IdentificationMatch(
                    speaker="turn-room-2",
                    match="Dylan",
                    confidence={"Dylan": 0.88},
                    start_seconds=0.8,
                    end_seconds=3.2,
                )
            ]
        }
    )
    assemblyai_client = FakeAssemblyAIClient({})
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client({"raw-audio/room-2/chunk-room-2.wav": room_audio}),
        deepgram_client=deepgram_client,
        pyannote_client=pyannote_client,
        assemblyai_client=assemblyai_client,
    )

    result = worker.run_once()

    session = session_factory()
    try:
        candidate = session.scalars(select(TranscriptCandidate)).one()
        canonical = session.scalars(select(CanonicalUtterance)).one()
    finally:
        session.close()

    assert result.processed_chunks == 1
    assert deepgram_client.calls == [room_audio]
    assert [call["audio_bytes"] for call in pyannote_client.calls] == [room_audio]
    assert assemblyai_client.calls == []
    assert candidate.vendor == "deepgram"
    assert canonical.speaker_name == "Dylan"


def test_pipeline_worker_room_keeps_unnamed_batch_pending_and_hidden(
    session_factory: sessionmaker[Session],
) -> None:
    room_chunks = _seed_room_uploaded_chunks(session_factory, count=20)
    room_audio_objects: dict[str, bytes] = {}
    room_frames = b""
    for index, (_chunk_id, key) in enumerate(room_chunks):
        wav_bytes, frames = _wav_chunk(index + 1)
        room_audio_objects[key] = wav_bytes
        room_frames += frames
    stitched_room_audio = _wav_bytes(room_frames)
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client(room_audio_objects),
        deepgram_client=FakeDeepgramClient({}),
        pyannote_client=FakePyannoteClient({}),
        assemblyai_client=FakeAssemblyAIClient(
            {
                stitched_room_audio: [
                    AssemblyAIUtterance(
                        vendor_segment_id="utt-room",
                        text="Still unnamed room turn.",
                        speaker_hint="A",
                        speaker_name=None,
                        confidence=0.84,
                        start_seconds=15.0,
                        end_seconds=45.0,
                        raw_payload={"speaker": "A"},
                    )
                ]
            }
        ),
        room_speakers=ROOM_SPEAKERS,
        room_assembly_window_seconds=600,
        room_assembly_idle_flush_seconds=120,
        now=lambda: datetime(2026, 4, 2, 13, 10, 5, tzinfo=UTC),
    )

    result = worker.run_once()

    session = session_factory()
    try:
        chunks = session.scalars(select(AudioChunk).order_by(AudioChunk.started_at)).all()
        candidates = session.scalars(select(TranscriptCandidate)).all()
        canonical = session.scalars(select(CanonicalUtterance)).all()
    finally:
        session.close()

    assert result.processed_chunks == 0
    assert result.failed_chunks == 0
    assert result.windows == 1
    assert [chunk.status for chunk in chunks] == ["uploaded"] * 20
    assert worker.deepgram_client.calls == []
    assert worker.pyannote_client.calls == []
    assert [call["audio_bytes"] for call in worker.assemblyai_client.calls] == [stitched_room_audio]
    assert candidates == []
    assert canonical == []


def test_pipeline_worker_room_keeps_room_batch_retryable_without_legacy_fallback_on_assemblyai_error(
    session_factory: sessionmaker[Session],
) -> None:
    room_chunks = _seed_room_uploaded_chunks(session_factory, count=20)
    room_audio_objects: dict[str, bytes] = {}
    room_frames = b""
    for index, (_chunk_id, key) in enumerate(room_chunks):
        wav_bytes, frames = _wav_chunk(index + 1)
        room_audio_objects[key] = wav_bytes
        room_frames += frames
    stitched_room_audio = _wav_bytes(room_frames)
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client(room_audio_objects),
        deepgram_client=FakeDeepgramClient({}),
        pyannote_client=FakePyannoteClient({}),
        assemblyai_client=FakeAssemblyAIClient(
            {
                stitched_room_audio: AssemblyAIClientError("unsupported audio"),
            }
        ),
        room_speakers=ROOM_SPEAKERS,
        room_assembly_window_seconds=600,
        room_assembly_idle_flush_seconds=120,
        now=lambda: datetime(2026, 4, 2, 13, 10, 5, tzinfo=UTC),
    )

    result = worker.run_once()

    session = session_factory()
    try:
        chunks = session.scalars(select(AudioChunk).order_by(AudioChunk.started_at)).all()
        candidates = session.scalars(select(TranscriptCandidate)).all()
        canonical = session.scalars(select(CanonicalUtterance)).all()
    finally:
        session.close()

    assert result.processed_chunks == 0
    assert result.failed_chunks == 0
    assert result.windows == 1
    assert [chunk.status for chunk in chunks] == ["uploaded"] * 20
    assert worker.deepgram_client.calls == []
    assert worker.pyannote_client.calls == []
    assert [call["audio_bytes"] for call in worker.assemblyai_client.calls] == [stitched_room_audio]
    assert candidates == []
    assert canonical == []
    assert all(chunk.error_message is None for chunk in chunks)


def test_pipeline_worker_keeps_exact_text_with_conflicting_named_speakers_separate(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_single_uploaded_chunk(session_factory)
    session = session_factory()
    try:
        session.add_all(
            [
                Source(id="room-2", source_type="room", device_owner=None),
                Voiceprint(
                    speaker_label="Dylan",
                    provider="pyannote",
                    provider_voiceprint_id="vp-1",
                    source_audio_key="voiceprints/dylan.wav",
                ),
                Voiceprint(
                    speaker_label="Alex",
                    provider="pyannote",
                    provider_voiceprint_id="vp-2",
                    source_audio_key="voiceprints/alex.wav",
                ),
                AudioChunk(
                    id="chunk-room-2",
                    source_id="room-2",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-2/chunk-room-2.wav",
                    status="uploaded",
                    started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    local_audio = b"local-audio"
    room_two_audio = b"room-two-audio"
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client(
            {
                "raw-audio/desk-a/chunk-local.wav": local_audio,
                "raw-audio/room-2/chunk-room-2.wav": room_two_audio,
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
                                "transcript": "We should ship it.",
                            }
                        ]
                    }
                },
                room_two_audio: {
                    "results": {
                        "utterances": [
                            {
                                "id": "utt-room-2",
                                "start": 7.0,
                                "end": 9.0,
                                "confidence": 0.84,
                                "speaker": 0,
                                "speaker_confidence": 0.74,
                                "transcript": "we should ship it",
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
                        speaker="turn-A",
                        match="Dylan",
                        confidence={"Dylan": 0.82},
                        start_seconds=0.8,
                        end_seconds=3.2,
                    )
                ],
                room_two_audio: [
                    IdentificationMatch(
                        speaker="turn-B",
                        match="Alex",
                        confidence={"Alex": 0.91},
                        start_seconds=7.0,
                        end_seconds=9.0,
                    )
                ]
            }
        ),
        assemblyai_client=FakeAssemblyAIClient({}),
    )

    result = worker.run_once()

    session = session_factory()
    try:
        canonical = session.scalars(select(CanonicalUtterance).order_by(CanonicalUtterance.started_at)).all()
        provenance = session.scalars(select(UtteranceSource).order_by(UtteranceSource.transcript_candidate_id)).all()
    finally:
        session.close()

    assert result.processed_chunks == 2
    assert len(canonical) == 2
    assert [row.speaker_name for row in canonical] == ["Dylan", "Alex"]
    assert len(provenance) == 2
    assert sum(row.is_canonical for row in provenance) == 2


def test_pipeline_worker_does_not_bridge_conflicting_named_speakers_through_unnamed_candidate(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_single_uploaded_chunk(session_factory)
    session = session_factory()
    try:
        session.add_all(
            [
                Source(id="room-2", source_type="room", device_owner=None),
                Source(id="room-3", source_type="room", device_owner=None),
                Voiceprint(
                    speaker_label="Dylan",
                    provider="pyannote",
                    provider_voiceprint_id="vp-1",
                    source_audio_key="voiceprints/dylan.wav",
                ),
                Voiceprint(
                    speaker_label="Alex",
                    provider="pyannote",
                    provider_voiceprint_id="vp-2",
                    source_audio_key="voiceprints/alex.wav",
                ),
                AudioChunk(
                    id="chunk-room-2",
                    source_id="room-2",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-2/chunk-room-2.wav",
                    status="uploaded",
                    started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
                ),
                AudioChunk(
                    id="chunk-room-3",
                    source_id="room-3",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-3/chunk-room-3.wav",
                    status="uploaded",
                    started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    local_audio = b"local-audio"
    room_two_audio = b"room-two-audio"
    room_three_audio = b"room-three-audio"
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client(
            {
                "raw-audio/desk-a/chunk-local.wav": local_audio,
                "raw-audio/room-2/chunk-room-2.wav": room_two_audio,
                "raw-audio/room-3/chunk-room-3.wav": room_three_audio,
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
                                "transcript": "We should ship it.",
                            }
                        ]
                    }
                },
                room_two_audio: {
                    "results": {
                        "utterances": [
                            {
                                "id": "utt-room-2",
                                "start": 1.4,
                                "end": 3.4,
                                "confidence": 0.85,
                                "speaker": 0,
                                "speaker_confidence": 0.78,
                                "transcript": "we should ship it",
                            }
                        ]
                    }
                },
                room_three_audio: {
                    "results": {
                        "utterances": [
                            {
                                "id": "utt-room-3",
                                "start": 1.2,
                                "end": 3.2,
                                "confidence": 0.84,
                                "speaker": 0,
                                "speaker_confidence": 0.72,
                                "transcript": "we should ship it",
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
                        speaker="turn-A",
                        match="Dylan",
                        confidence={"Dylan": 0.82},
                        start_seconds=0.8,
                        end_seconds=3.2,
                    )
                ],
                room_two_audio: [
                    IdentificationMatch(
                        speaker="turn-C",
                        match="Alex",
                        confidence={"Alex": 0.91},
                        start_seconds=1.2,
                        end_seconds=3.4,
                    )
                ],
                room_three_audio: [
                    IdentificationMatch(
                        speaker="turn-D",
                        match=None,
                        confidence={},
                        start_seconds=1.2,
                        end_seconds=3.2,
                    )
                ]
            }
        ),
        assemblyai_client=FakeAssemblyAIClient({}),
    )

    result = worker.run_once()

    session = session_factory()
    try:
        canonical = session.scalars(select(CanonicalUtterance).order_by(CanonicalUtterance.started_at)).all()
        provenance = session.scalars(select(UtteranceSource).order_by(UtteranceSource.transcript_candidate_id)).all()
    finally:
        session.close()

    assert result.processed_chunks == 3
    assert len(canonical) == 2
    assert [row.speaker_name for row in canonical] == ["Dylan", "Alex"]
    assert len(provenance) == 3
    assert sum(row.is_canonical for row in provenance) == 2


def test_pipeline_worker_matches_speakers_by_time_overlap_not_label_namespace(
    session_factory: sessionmaker[Session],
) -> None:
    _seed_single_uploaded_chunk(session_factory)
    session = session_factory()
    try:
        session.add(
            Voiceprint(
                speaker_label="Dylan",
                provider="pyannote",
                provider_voiceprint_id="vp-1",
                source_audio_key="voiceprints/dylan.wav",
            )
        )
        session.commit()
    finally:
        session.close()

    local_audio = b"local-audio"
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client({"raw-audio/desk-a/chunk-local.wav": local_audio}),
        deepgram_client=FakeDeepgramClient(
            {
                local_audio: {
                    "results": {
                        "utterances": [
                            {
                                "id": "utt-local",
                                "start": 4.0,
                                "end": 7.0,
                                "confidence": 0.94,
                                "speaker": 0,
                                "speaker_confidence": 0.82,
                                "transcript": "I am here.",
                            }
                        ]
                    }
                }
            }
        ),
        pyannote_client=FakePyannoteClient(
            {
                local_audio: [
                    IdentificationMatch(
                        speaker="pyannote:alpha",
                        match="Dylan",
                        confidence={"Dylan": 0.88},
                        start_seconds=4.2,
                        end_seconds=6.8,
                    )
                ]
            }
        ),
    )

    result = worker.run_once()

    session = session_factory()
    try:
        candidate = session.scalars(select(TranscriptCandidate)).one()
        canonical = session.scalars(select(CanonicalUtterance)).one()
    finally:
        session.close()

    assert result.processed_chunks == 1
    assert candidate.speaker_hint == "speaker_0"
    assert candidate.speaker_confidence == pytest.approx(0.98)
    assert canonical.speaker_name == "Dylan"


def test_pipeline_worker_allows_non_room_1_room_source_device_owner_metadata_without_suppressing_human_match(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    try:
        session.add_all(
            [
                Source(id="room-2", source_type="room", device_owner="conference-room"),
                Voiceprint(
                    speaker_label="Dylan",
                    provider="pyannote",
                    provider_voiceprint_id="vp-1",
                    source_audio_key="voiceprints/dylan.wav",
                ),
                AudioChunk(
                    id="chunk-room",
                    source_id="room-2",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-2/chunk-room.wav",
                    status="uploaded",
                    started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    room_audio = b"room-audio"
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client({"raw-audio/room-2/chunk-room.wav": room_audio}),
        deepgram_client=FakeDeepgramClient(
            {
                room_audio: {
                    "results": {
                        "utterances": [
                            {
                                "id": "utt-room",
                                "start": 1.0,
                                "end": 3.0,
                                "confidence": 0.84,
                                "speaker": 0,
                                "speaker_confidence": 0.74,
                                "transcript": "Hello there.",
                            }
                        ]
                    }
                }
            }
        ),
        pyannote_client=FakePyannoteClient(
            {
                room_audio: [
                    IdentificationMatch(
                        speaker="speaker#room-17",
                        match="Dylan",
                        confidence={"Dylan": 0.88},
                        start_seconds=0.8,
                        end_seconds=3.2,
                    )
                ]
            }
        ),
        assemblyai_client=FakeAssemblyAIClient({}),
    )

    result = worker.run_once()

    session = session_factory()
    try:
        canonical = session.scalars(select(CanonicalUtterance)).one()
    finally:
        session.close()

    assert result.processed_chunks == 1
    assert canonical.speaker_name == "Dylan"


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


def test_pipeline_worker_prioritizes_recently_uploaded_windows_over_older_backlog(
    session_factory: sessionmaker[Session],
) -> None:
    session = session_factory()
    try:
        session.add_all(
            [
                Source(id="desk-a", source_type="macbook", device_owner="Dylan"),
                AudioChunk(
                    id="chunk-old",
                    source_id="desk-a",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/desk-a/chunk-old.wav",
                    status="uploaded",
                    started_at=datetime(2026, 4, 5, 23, 0, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 5, 23, 0, 30, tzinfo=UTC),
                    uploaded_at=datetime(2026, 4, 6, 16, 52, 0, tzinfo=UTC),
                ),
                AudioChunk(
                    id="chunk-recent",
                    source_id="desk-a",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/desk-a/chunk-recent.wav",
                    status="uploaded",
                    started_at=datetime(2026, 4, 6, 16, 50, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 4, 6, 16, 50, 30, tzinfo=UTC),
                    uploaded_at=datetime(2026, 4, 6, 16, 51, 0, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    old_audio = b"old-audio"
    recent_audio = b"recent-audio"
    deepgram_client = FakeDeepgramClient(
        {
            old_audio: {"results": {"utterances": []}},
            recent_audio: {"results": {"utterances": []}},
        }
    )
    worker = PipelineWorker(
        session_factory=session_factory,
        s3_client=FakeS3Client(
            {
                "raw-audio/desk-a/chunk-old.wav": old_audio,
                "raw-audio/desk-a/chunk-recent.wav": recent_audio,
            }
        ),
        deepgram_client=deepgram_client,
        pyannote_client=FakePyannoteClient(
            {
                old_audio: [],
                recent_audio: [],
            }
        ),
    )

    result = worker.run_once()

    assert result.processed_chunks == 2
    assert deepgram_client.calls == [recent_audio, old_audio]
