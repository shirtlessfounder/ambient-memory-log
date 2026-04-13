from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
import importlib
import wave

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.models import AudioChunk, Base, CanonicalUtterance, Source, TranscriptCandidate, UtteranceSource


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(TSVECTOR, "sqlite")
def _compile_tsvector_sqlite(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


def _import_room_track_audio_module():
    try:
        return importlib.import_module("ambient_memory.pipeline.room_track_audio")
    except ModuleNotFoundError as exc:
        pytest.fail(f"room track audio module missing: {exc}")


def _at(offset_seconds: float) -> datetime:
    return datetime(2026, 4, 10, 13, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)


def _wav_bytes(samples: list[int], *, frame_rate: int = 10) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        frames = b"".join(int(sample).to_bytes(2, byteorder="little", signed=True) for sample in samples)
        wav_file.writeframes(frames)
    return buffer.getvalue()


def _read_samples(audio_bytes: bytes) -> list[int]:
    with wave.open(BytesIO(audio_bytes), "rb") as wav_file:
        frame_count = wav_file.getnframes()
        frames = wav_file.readframes(frame_count)

    return [
        int.from_bytes(frames[index : index + 2], byteorder="little", signed=True)
        for index in range(0, len(frames), 2)
    ]


class FakeS3Client:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.calls: list[dict[str, str]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, BytesIO]:
        self.calls.append({"Bucket": Bucket, "Key": Key})
        return {"Body": BytesIO(self.objects[Key])}


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_load_room_provenance_slices_only_keeps_room_1_rows_and_prefers_canonical_room_candidate(
    session_factory: sessionmaker[Session],
) -> None:
    room_track_audio = _import_room_track_audio_module()
    session = session_factory()
    try:
        session.add_all(
            [
                Source(id="desk-a", source_type="macbook", device_owner="Dylan"),
                Source(id="room-1", source_type="room", device_owner=None),
                Source(id="room-2", source_type="room", device_owner=None),
                AudioChunk(
                    id="chunk-room-canonical",
                    source_id="room-1",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-1/canonical.wav",
                    status="uploaded",
                    started_at=_at(0.0),
                    ended_at=_at(30.0),
                ),
                AudioChunk(
                    id="chunk-room-secondary",
                    source_id="room-1",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-1/secondary.wav",
                    status="uploaded",
                    started_at=_at(0.0),
                    ended_at=_at(30.0),
                ),
                AudioChunk(
                    id="chunk-desk",
                    source_id="desk-a",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/desk-a/chunk.wav",
                    status="uploaded",
                    started_at=_at(0.0),
                    ended_at=_at(30.0),
                ),
                AudioChunk(
                    id="chunk-room-2",
                    source_id="room-2",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-2/chunk.wav",
                    status="uploaded",
                    started_at=_at(0.0),
                    ended_at=_at(30.0),
                ),
                CanonicalUtterance(
                    id="utt-canonical-room",
                    text="room canonical",
                    started_at=_at(1.0),
                    ended_at=_at(2.0),
                    speaker_name="A",
                    canonical_source_id="room-1",
                    processing_version="v1",
                ),
                CanonicalUtterance(
                    id="utt-mixed",
                    text="mixed provenance",
                    started_at=_at(3.0),
                    ended_at=_at(4.0),
                    speaker_name="B",
                    canonical_source_id="room-1",
                    processing_version="v1",
                ),
                CanonicalUtterance(
                    id="utt-room-2",
                    text="other room",
                    started_at=_at(5.0),
                    ended_at=_at(6.0),
                    speaker_name="D",
                    canonical_source_id="room-2",
                    processing_version="v1",
                ),
            ]
        )
        session.flush()

        session.add_all(
            [
                TranscriptCandidate(
                    id="cand-room-canonical",
                    audio_chunk_id="chunk-room-canonical",
                    source_id="room-1",
                    vendor="assemblyai",
                    vendor_segment_id="seg-room-canonical",
                    text="room canonical",
                    speaker_hint="A",
                    speaker_confidence=None,
                    confidence=0.93,
                    started_at=_at(1.0),
                    ended_at=_at(2.0),
                    raw_payload={"kind": "room-canonical"},
                ),
                TranscriptCandidate(
                    id="cand-room-secondary",
                    audio_chunk_id="chunk-room-secondary",
                    source_id="room-1",
                    vendor="assemblyai",
                    vendor_segment_id="seg-room-secondary",
                    text="room alternate",
                    speaker_hint="C",
                    speaker_confidence=None,
                    confidence=0.72,
                    started_at=_at(1.0),
                    ended_at=_at(2.0),
                    raw_payload={"kind": "room-secondary"},
                ),
                TranscriptCandidate(
                    id="cand-desk-canonical",
                    audio_chunk_id="chunk-desk",
                    source_id="desk-a",
                    vendor="deepgram",
                    vendor_segment_id="seg-desk",
                    text="desk canonical",
                    speaker_hint="speaker_0",
                    speaker_confidence=0.91,
                    confidence=0.91,
                    started_at=_at(3.0),
                    ended_at=_at(4.0),
                    raw_payload={"kind": "desk-canonical"},
                ),
                TranscriptCandidate(
                    id="cand-room-mixed",
                    audio_chunk_id="chunk-room-secondary",
                    source_id="room-1",
                    vendor="assemblyai",
                    vendor_segment_id="seg-room-mixed",
                    text="room mixed",
                    speaker_hint="B",
                    speaker_confidence=None,
                    confidence=0.77,
                    started_at=_at(3.0),
                    ended_at=_at(4.0),
                    raw_payload={"kind": "room-mixed"},
                ),
                TranscriptCandidate(
                    id="cand-room-2",
                    audio_chunk_id="chunk-room-2",
                    source_id="room-2",
                    vendor="assemblyai",
                    vendor_segment_id="seg-room-2",
                    text="other room",
                    speaker_hint="D",
                    speaker_confidence=None,
                    confidence=0.84,
                    started_at=_at(5.0),
                    ended_at=_at(6.0),
                    raw_payload={"kind": "room-2"},
                ),
            ]
        )
        session.flush()

        session.add_all(
            [
                UtteranceSource(
                    canonical_utterance_id="utt-canonical-room",
                    transcript_candidate_id="cand-room-secondary",
                    is_canonical=False,
                ),
                UtteranceSource(
                    canonical_utterance_id="utt-canonical-room",
                    transcript_candidate_id="cand-room-canonical",
                    is_canonical=True,
                ),
                UtteranceSource(
                    canonical_utterance_id="utt-mixed",
                    transcript_candidate_id="cand-desk-canonical",
                    is_canonical=True,
                ),
                UtteranceSource(
                    canonical_utterance_id="utt-mixed",
                    transcript_candidate_id="cand-room-mixed",
                    is_canonical=False,
                ),
                UtteranceSource(
                    canonical_utterance_id="utt-room-2",
                    transcript_candidate_id="cand-room-2",
                    is_canonical=True,
                ),
            ]
        )
        session.commit()

        slices = room_track_audio.load_room_provenance_slices(
            session,
            source_id="room-1",
            window_started_at=_at(0.0),
            window_ended_at=_at(900.0),
        )
    finally:
        session.close()

    assert [item.canonical_utterance_id for item in slices] == ["utt-canonical-room", "utt-mixed"]
    assert [item.transcript_candidate_id for item in slices] == ["cand-room-canonical", "cand-room-mixed"]
    assert [item.raw_track_label for item in slices] == ["A", "B"]
    assert [item.audio_chunk_id for item in slices] == ["chunk-room-canonical", "chunk-room-secondary"]
    assert all(item.source_id == "room-1" for item in slices)


def test_load_room_provenance_slices_falls_back_to_raw_payload_speaker_when_speaker_hint_missing(
    session_factory: sessionmaker[Session],
) -> None:
    room_track_audio = _import_room_track_audio_module()
    session = session_factory()
    try:
        session.add_all(
            [
                Source(id="room-1", source_type="room", device_owner=None),
                AudioChunk(
                    id="chunk-room-1",
                    source_id="room-1",
                    s3_bucket="ambient-memory",
                    s3_key="raw-audio/room-1/chunk.wav",
                    status="uploaded",
                    started_at=_at(0.0),
                    ended_at=_at(30.0),
                ),
                CanonicalUtterance(
                    id="utt-room-null-hint",
                    text="room provenance with null hint",
                    started_at=_at(1.0),
                    ended_at=_at(2.0),
                    speaker_name=None,
                    canonical_source_id="room-1",
                    processing_version="v1",
                ),
            ]
        )
        session.flush()
        session.add(
            TranscriptCandidate(
                id="cand-room-null-hint",
                audio_chunk_id="chunk-room-1",
                source_id="room-1",
                vendor="assemblyai",
                vendor_segment_id="seg-room-null-hint",
                text="room provenance with null hint",
                speaker_hint=None,
                speaker_confidence=None,
                confidence=0.88,
                started_at=_at(1.0),
                ended_at=_at(2.0),
                raw_payload={"speaker": "Alexander"},
            )
        )
        session.flush()
        session.add(
            UtteranceSource(
                canonical_utterance_id="utt-room-null-hint",
                transcript_candidate_id="cand-room-null-hint",
                is_canonical=True,
            )
        )
        session.commit()

        slices = room_track_audio.load_room_provenance_slices(
            session,
            source_id="room-1",
            window_started_at=_at(0.0),
            window_ended_at=_at(900.0),
        )
    finally:
        session.close()

    assert [item.canonical_utterance_id for item in slices] == ["utt-room-null-hint"]
    assert [item.raw_track_label for item in slices] == ["Alexander"]


def test_build_room_window_audio_slices_wav_by_utterance_time_and_stitches_full_window_and_track_bundles() -> None:
    room_track_audio = _import_room_track_audio_module()
    chunk_one_key = "raw-audio/room-1/chunk-1.wav"
    chunk_two_key = "raw-audio/room-1/chunk-2.wav"
    s3_client = FakeS3Client(
        {
            chunk_one_key: _wav_bytes([1, 2, 3, 4, 5, 6]),
            chunk_two_key: _wav_bytes([7, 8, 9, 10]),
        }
    )

    window_audio = room_track_audio.build_room_window_audio(
        (
            room_track_audio.RoomProvenanceSlice(
                canonical_utterance_id="utt-1",
                transcript_candidate_id="cand-1",
                source_id="room-1",
                raw_track_label="A",
                utterance_started_at=_at(0.0),
                utterance_ended_at=_at(0.2),
                audio_chunk_id="chunk-1",
                audio_chunk_started_at=_at(0.0),
                audio_chunk_ended_at=_at(0.6),
                s3_bucket="ambient-memory",
                s3_key=chunk_one_key,
            ),
            room_track_audio.RoomProvenanceSlice(
                canonical_utterance_id="utt-2",
                transcript_candidate_id="cand-2",
                source_id="room-1",
                raw_track_label="B",
                utterance_started_at=_at(0.2),
                utterance_ended_at=_at(0.4),
                audio_chunk_id="chunk-1",
                audio_chunk_started_at=_at(0.0),
                audio_chunk_ended_at=_at(0.6),
                s3_bucket="ambient-memory",
                s3_key=chunk_one_key,
            ),
            room_track_audio.RoomProvenanceSlice(
                canonical_utterance_id="utt-3",
                transcript_candidate_id="cand-3",
                source_id="room-1",
                raw_track_label="A",
                utterance_started_at=_at(1.0),
                utterance_ended_at=_at(1.2),
                audio_chunk_id="chunk-2",
                audio_chunk_started_at=_at(1.0),
                audio_chunk_ended_at=_at(1.4),
                s3_bucket="ambient-memory",
                s3_key=chunk_two_key,
            ),
        ),
        s3_client=s3_client,
    )

    assert _read_samples(window_audio.audio_bytes) == [1, 2, 3, 4, 7, 8]
    assert [bundle.raw_track_label for bundle in window_audio.track_bundles] == ["A", "B"]
    assert [bundle.utterance_ids for bundle in window_audio.track_bundles] == [("utt-1", "utt-3"), ("utt-2",)]
    assert _read_samples(window_audio.track_bundles[0].audio_bytes) == [1, 2, 7, 8]
    assert _read_samples(window_audio.track_bundles[1].audio_bytes) == [3, 4]
    assert [call["Key"] for call in s3_client.calls] == [chunk_one_key, chunk_two_key]


def test_build_room_window_audio_measures_pooled_speech_seconds_per_track(monkeypatch: pytest.MonkeyPatch) -> None:
    room_track_audio = _import_room_track_audio_module()
    chunk_one_key = "raw-audio/room-1/chunk-1.wav"
    chunk_two_key = "raw-audio/room-1/chunk-2.wav"
    speech_calls: list[tuple[int, ...]] = []

    def fake_measure_speech_seconds(audio_bytes: bytes) -> float:
        samples = tuple(_read_samples(audio_bytes))
        speech_calls.append(samples)
        return {
            (1, 2, 7, 8): 3.25,
            (3, 4): 1.5,
        }[samples]

    monkeypatch.setattr(room_track_audio, "measure_speech_seconds", fake_measure_speech_seconds)

    window_audio = room_track_audio.build_room_window_audio(
        (
            room_track_audio.RoomProvenanceSlice(
                canonical_utterance_id="utt-1",
                transcript_candidate_id="cand-1",
                source_id="room-1",
                raw_track_label="A",
                utterance_started_at=_at(0.0),
                utterance_ended_at=_at(0.2),
                audio_chunk_id="chunk-1",
                audio_chunk_started_at=_at(0.0),
                audio_chunk_ended_at=_at(0.6),
                s3_bucket="ambient-memory",
                s3_key=chunk_one_key,
            ),
            room_track_audio.RoomProvenanceSlice(
                canonical_utterance_id="utt-2",
                transcript_candidate_id="cand-2",
                source_id="room-1",
                raw_track_label="B",
                utterance_started_at=_at(0.2),
                utterance_ended_at=_at(0.4),
                audio_chunk_id="chunk-1",
                audio_chunk_started_at=_at(0.0),
                audio_chunk_ended_at=_at(0.6),
                s3_bucket="ambient-memory",
                s3_key=chunk_one_key,
            ),
            room_track_audio.RoomProvenanceSlice(
                canonical_utterance_id="utt-3",
                transcript_candidate_id="cand-3",
                source_id="room-1",
                raw_track_label="A",
                utterance_started_at=_at(1.0),
                utterance_ended_at=_at(1.2),
                audio_chunk_id="chunk-2",
                audio_chunk_started_at=_at(1.0),
                audio_chunk_ended_at=_at(1.4),
                s3_bucket="ambient-memory",
                s3_key=chunk_two_key,
            ),
        ),
        s3_client=FakeS3Client(
            {
                chunk_one_key: _wav_bytes([1, 2, 3, 4, 5, 6]),
                chunk_two_key: _wav_bytes([7, 8, 9, 10]),
            }
        ),
    )

    assert [bundle.speech_seconds for bundle in window_audio.track_bundles] == [3.25, 1.5]
    assert speech_calls == [(1, 2, 7, 8), (3, 4)]
