from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import importlib
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.integrations.pyannote_client import PyannoteError, VoiceprintReference
from ambient_memory.models import Base, CanonicalUtterance, Source


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(TSVECTOR, "sqlite")
def _compile_tsvector_sqlite(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


def _import_room_enrichment_module():
    try:
        return importlib.import_module("ambient_memory.pipeline.room_enrichment")
    except ModuleNotFoundError as exc:
        pytest.fail(f"room enrichment module missing: {exc}")


def _canonical_enrichment_model():
    from ambient_memory import models

    row_type = getattr(models, "CanonicalUtteranceEnrichment", None)
    if row_type is None:
        pytest.fail("CanonicalUtteranceEnrichment model missing")
    return row_type


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed_canonical_utterances(
    session_factory: sessionmaker[Session],
    *,
    source_id: str = "room-1",
    utterances: list[dict[str, object]],
) -> None:
    session = session_factory()
    try:
        source = session.get(Source, source_id)
        if source is None:
            session.add(Source(id=source_id, source_type="room", device_owner=None))

        for utterance in utterances:
            session.add(
                CanonicalUtterance(
                    id=utterance["id"],
                    canonical_source_id=source_id,
                    text=utterance["text"],
                    speaker_name=utterance.get("speaker_name"),
                    speaker_confidence=utterance.get("speaker_confidence"),
                    raw_speaker_name=utterance.get("speaker_name"),
                    raw_speaker_confidence=utterance.get("speaker_confidence"),
                    started_at=utterance["started_at"],
                    ended_at=utterance["ended_at"],
                    processing_version="v1",
                )
            )
        session.commit()
    finally:
        session.close()


@dataclass(frozen=True, slots=True)
class FakeProvenanceSlice:
    canonical_utterance_id: str
    raw_track_label: str


@dataclass(frozen=True, slots=True)
class FakeTrackBundle:
    raw_track_label: str
    audio_bytes: bytes
    speech_seconds: float


@dataclass(frozen=True, slots=True)
class FakeRoomWindowAudio:
    audio_bytes: bytes
    track_bundles: tuple[FakeTrackBundle, ...]


@dataclass(frozen=True, slots=True)
class FakeResolvedTrackIdentity:
    raw_track_label: str
    resolved_identity: str
    identity_method: str
    top_match_label: str | None
    top_match_confidence: float | None
    second_match_label: str | None
    second_match_confidence: float | None


@dataclass(frozen=True, slots=True)
class FakeAlignedTranscriptRow:
    canonical_utterance_id: str
    text: str
    confidence: float | None
    used_raw_fallback: bool


class FakeRetranscriptionClient:
    vendor = "openai"

    def __init__(self, segments: tuple[object, ...]) -> None:
        self.segments = list(segments)
        self.calls: list[dict[str, object]] = []

    def transcribe_window(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        window_started_at: datetime,
        content_type: str = "audio/wav",
    ) -> list[object]:
        self.calls.append(
            {
                "audio_bytes": audio_bytes,
                "filename": filename,
                "window_started_at": window_started_at,
                "content_type": content_type,
            }
        )
        return list(self.segments)


def _room_v2_settings() -> SimpleNamespace:
    return SimpleNamespace(
        openai_api_key="openai-key",
        aws_region="us-east-1",
        pyannote_api_key="pyannote-key",
        openai_audio_transcribe_model="gpt-4o-transcribe-diarize",
        room_track_min_speech_seconds=8.0,
        room_track_match_threshold=0.75,
        room_track_match_margin=0.15,
    )


def test_room_enrichment_orchestrates_room_v2_persistence_and_audit_fields(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room_enrichment = _import_room_enrichment_module()
    enrichment_model = _canonical_enrichment_model()

    assert room_enrichment.DEFAULT_ROOM_ENRICHMENT_RESOLVER_VERSION == "room-v2-audio-identity-v1"

    window_start = datetime(2026, 4, 10, 14, 0, tzinfo=UTC)
    _seed_canonical_utterances(
        session_factory,
        utterances=[
            {
                "id": "utt-1",
                "text": "we should ship it after lunch",
                "speaker_name": "A",
                "speaker_confidence": 0.61,
                "started_at": window_start + timedelta(minutes=2),
                "ended_at": window_start + timedelta(minutes=2, seconds=5),
            },
            {
                "id": "utt-2",
                "text": "i can take the follow up",
                "speaker_name": "A",
                "speaker_confidence": 0.61,
                "started_at": window_start + timedelta(minutes=4),
                "ended_at": window_start + timedelta(minutes=4, seconds=5),
            },
            {
                "id": "utt-3",
                "text": "i will just listen",
                "speaker_name": "B",
                "speaker_confidence": 0.58,
                "started_at": window_start + timedelta(minutes=7),
                "ended_at": window_start + timedelta(minutes=7, seconds=3),
            },
            {
                "id": "utt-4",
                "text": "thanks for having me",
                "speaker_name": "C",
                "speaker_confidence": 0.54,
                "started_at": window_start + timedelta(minutes=9),
                "ended_at": window_start + timedelta(minutes=9, seconds=4),
            },
        ],
    )

    session = session_factory()
    try:
        before_rows = session.scalars(select(CanonicalUtterance).order_by(CanonicalUtterance.started_at)).all()
        before_snapshot = {
            row.id: (
                row.text,
                row.speaker_name,
                row.speaker_confidence,
                row.started_at,
                row.ended_at,
            )
            for row in before_rows
        }
    finally:
        session.close()

    provenance_slices = (
        FakeProvenanceSlice(canonical_utterance_id="utt-1", raw_track_label="A"),
        FakeProvenanceSlice(canonical_utterance_id="utt-2", raw_track_label="A"),
        FakeProvenanceSlice(canonical_utterance_id="utt-3", raw_track_label="B"),
        FakeProvenanceSlice(canonical_utterance_id="utt-4", raw_track_label="C"),
    )
    window_audio = FakeRoomWindowAudio(
        audio_bytes=b"window-audio",
        track_bundles=(
            FakeTrackBundle(raw_track_label="A", audio_bytes=b"track-a", speech_seconds=13.2),
            FakeTrackBundle(raw_track_label="B", audio_bytes=b"track-b", speech_seconds=3.1),
            FakeTrackBundle(raw_track_label="C", audio_bytes=b"track-c", speech_seconds=10.4),
        ),
    )
    track_identities = (
        FakeResolvedTrackIdentity(
            raw_track_label="A",
            resolved_identity="Dylan",
            identity_method="pyannote-teammate",
            top_match_label="Dylan",
            top_match_confidence=0.94,
            second_match_label="Alex",
            second_match_confidence=0.62,
        ),
        FakeResolvedTrackIdentity(
            raw_track_label="B",
            resolved_identity="unknown",
            identity_method="speech-too-short",
            top_match_label="Niyant",
            top_match_confidence=0.78,
            second_match_label="Alex",
            second_match_confidence=0.33,
        ),
        FakeResolvedTrackIdentity(
            raw_track_label="C",
            resolved_identity="external-1",
            identity_method="pyannote-external",
            top_match_label="Dylan",
            top_match_confidence=0.52,
            second_match_label="Alex",
            second_match_confidence=0.29,
        ),
    )
    aligned_rows = (
        FakeAlignedTranscriptRow(
            canonical_utterance_id="utt-1",
            text="We should ship it after lunch.",
            confidence=0.91,
            used_raw_fallback=False,
        ),
        FakeAlignedTranscriptRow(
            canonical_utterance_id="utt-2",
            text="I can take the follow-up.",
            confidence=0.83,
            used_raw_fallback=False,
        ),
        FakeAlignedTranscriptRow(
            canonical_utterance_id="utt-3",
            text="i will just listen",
            confidence=None,
            used_raw_fallback=True,
        ),
        FakeAlignedTranscriptRow(
            canonical_utterance_id="utt-4",
            text="Thanks for having me.",
            confidence=0.79,
            used_raw_fallback=False,
        ),
    )
    segments = ("segment-1", "segment-2")
    calls: dict[str, object] = {}

    def fake_load_room_provenance_slices(session, *, source_id, window_started_at, window_ended_at):
        del session
        calls["load_room_provenance_slices"] = {
            "source_id": source_id,
            "window_started_at": window_started_at,
            "window_ended_at": window_ended_at,
        }
        return provenance_slices

    def fake_build_room_window_audio(provenance_rows, *, s3_client, speech_seconds_measure=None):
        calls["build_room_window_audio"] = {
            "provenance_rows": tuple(provenance_rows),
            "s3_client": s3_client,
            "speech_seconds_measure": speech_seconds_measure,
        }
        return window_audio

    def fake_resolve_track_identities(
        *,
        track_bundles,
        pyannote_client,
        voiceprints,
        minimum_pooled_speech_seconds,
        teammate_threshold,
        top_vs_second_margin,
    ):
        calls["resolve_track_identities"] = {
            "track_bundles": tuple(track_bundles),
            "pyannote_client": pyannote_client,
            "voiceprints": tuple(voiceprints),
            "minimum_pooled_speech_seconds": minimum_pooled_speech_seconds,
            "teammate_threshold": teammate_threshold,
            "top_vs_second_margin": top_vs_second_margin,
        }
        return track_identities

    def fake_align_retranscribed_segments(utterances, segments_value, *, window_started_at):
        calls["align_retranscribed_segments"] = {
            "utterance_ids": [utterance.canonical_utterance_id for utterance in utterances],
            "raw_texts": [utterance.raw_text for utterance in utterances],
            "segments": tuple(segments_value),
            "window_started_at": window_started_at,
        }
        return list(aligned_rows)

    monkeypatch.setattr(
        room_enrichment,
        "load_room_provenance_slices",
        fake_load_room_provenance_slices,
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "build_room_window_audio",
        fake_build_room_window_audio,
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "resolve_track_identities",
        fake_resolve_track_identities,
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "align_retranscribed_segments",
        fake_align_retranscribed_segments,
        raising=False,
    )

    retranscription_client = FakeRetranscriptionClient(segments=segments)
    s3_client = object()
    pyannote_client = object()
    voiceprints = (
        VoiceprintReference(label="Dylan", voiceprint="vp-dylan"),
        VoiceprintReference(label="Alex", voiceprint="vp-alex"),
    )

    result = room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        source_id="room-1",
        hours=4,
        resolver_version="room-v2-audio-identity-v1",
        dry_run=False,
        settings=_room_v2_settings(),
        now=lambda: datetime(2026, 4, 10, 16, 0, tzinfo=UTC),
        s3_client=s3_client,
        pyannote_client=pyannote_client,
        retranscription_client=retranscription_client,
        voiceprints=voiceprints,
    )

    session = session_factory()
    try:
        canonical_rows = session.scalars(select(CanonicalUtterance).order_by(CanonicalUtterance.started_at)).all()
        enrichment_rows = session.scalars(
            select(enrichment_model).order_by(enrichment_model.canonical_utterance_id)
        ).all()
    finally:
        session.close()

    assert result.dry_run is False
    assert result.windows == 1
    assert result.utterances == 4
    assert result.created == 4
    assert calls["load_room_provenance_slices"] == {
        "source_id": "room-1",
        "window_started_at": window_start,
        "window_ended_at": window_start + timedelta(minutes=15),
    }
    assert calls["build_room_window_audio"] == {
        "provenance_rows": provenance_slices,
        "s3_client": s3_client,
        "speech_seconds_measure": None,
    }
    assert calls["resolve_track_identities"] == {
        "track_bundles": window_audio.track_bundles,
        "pyannote_client": pyannote_client,
        "voiceprints": voiceprints,
        "minimum_pooled_speech_seconds": 8.0,
        "teammate_threshold": 0.75,
        "top_vs_second_margin": 0.15,
    }
    assert calls["align_retranscribed_segments"] == {
        "utterance_ids": ["utt-1", "utt-2", "utt-3", "utt-4"],
        "raw_texts": [
            "we should ship it after lunch",
            "i can take the follow up",
            "i will just listen",
            "thanks for having me",
        ],
        "segments": segments,
        "window_started_at": window_start,
    }
    assert retranscription_client.calls == [
        {
            "audio_bytes": b"window-audio",
            "filename": "room-1-20260410T140000Z.wav",
            "window_started_at": window_start,
            "content_type": "audio/wav",
        }
    ]
    assert [row.id for row in canonical_rows] == ["utt-1", "utt-2", "utt-3", "utt-4"]
    assert {
        row.id: (
            row.text,
            row.started_at,
            row.ended_at,
            row.raw_speaker_name,
            row.raw_speaker_confidence,
            row.speaker_name,
            row.speaker_confidence,
        )
        for row in canonical_rows
    } == {
        "utt-1": (
            before_snapshot["utt-1"][0],
            before_snapshot["utt-1"][3],
            before_snapshot["utt-1"][4],
            "A",
            0.61,
            "Dylan",
            0.94,
        ),
        "utt-2": (
            before_snapshot["utt-2"][0],
            before_snapshot["utt-2"][3],
            before_snapshot["utt-2"][4],
            "A",
            0.61,
            "Dylan",
            0.94,
        ),
        "utt-3": (
            before_snapshot["utt-3"][0],
            before_snapshot["utt-3"][3],
            before_snapshot["utt-3"][4],
            "B",
            0.58,
            "unknown",
            0.78,
        ),
        "utt-4": (
            before_snapshot["utt-4"][0],
            before_snapshot["utt-4"][3],
            before_snapshot["utt-4"][4],
            "C",
            0.54,
            "external-1",
            0.52,
        ),
    }

    persisted = {
        row.canonical_utterance_id: row
        for row in enrichment_rows
    }
    assert sorted(persisted) == ["utt-1", "utt-2", "utt-3", "utt-4"]
    assert persisted["utt-1"].resolved_speaker_name == "Dylan"
    assert persisted["utt-2"].resolved_speaker_name == "Dylan"
    assert persisted["utt-3"].resolved_speaker_name == "unknown"
    assert persisted["utt-4"].resolved_speaker_name == "external-1"
    assert persisted["utt-1"].resolved_speaker_confidence == 0.94
    assert persisted["utt-3"].resolved_speaker_confidence == 0.78
    assert persisted["utt-4"].resolved_speaker_confidence == 0.52
    assert persisted["utt-1"].cleaned_text == "We should ship it after lunch."
    assert persisted["utt-2"].cleaned_text == "I can take the follow-up."
    assert persisted["utt-3"].cleaned_text == "i will just listen"
    assert persisted["utt-4"].cleaned_text == "Thanks for having me."
    assert persisted["utt-1"].identity_method == "pyannote-teammate"
    assert persisted["utt-3"].identity_method == "speech-too-short"
    assert persisted["utt-4"].identity_method == "pyannote-external"
    assert persisted["utt-1"].identity_track_label == "A"
    assert persisted["utt-2"].identity_track_label == "A"
    assert persisted["utt-3"].identity_track_label == "B"
    assert persisted["utt-4"].identity_track_label == "C"
    assert persisted["utt-1"].identity_window_started_at == window_start.replace(tzinfo=None)
    assert persisted["utt-4"].identity_window_started_at == window_start.replace(tzinfo=None)
    assert persisted["utt-1"].identity_match_label == "Dylan"
    assert persisted["utt-1"].identity_match_confidence == 0.94
    assert persisted["utt-1"].identity_second_match_label == "Alex"
    assert persisted["utt-1"].identity_second_match_confidence == 0.62
    assert persisted["utt-3"].identity_match_label == "Niyant"
    assert persisted["utt-4"].identity_match_label == "Dylan"
    assert persisted["utt-1"].transcript_method == "openai-retranscribe"
    assert persisted["utt-3"].transcript_method == "raw-canonical-fallback"
    assert persisted["utt-4"].transcript_method == "openai-retranscribe"
    assert persisted["utt-1"].transcript_confidence == 0.91
    assert persisted["utt-2"].transcript_confidence == 0.83
    assert persisted["utt-3"].transcript_confidence is None
    assert persisted["utt-4"].transcript_confidence == 0.79
    assert persisted["utt-3"].resolution_notes == "transcript alignment fell back to raw canonical text"
    assert persisted["utt-1"].resolution_notes is None


def test_room_enrichment_rerun_is_idempotent_for_same_resolver_version(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room_enrichment = _import_room_enrichment_module()
    enrichment_model = _canonical_enrichment_model()
    start = datetime(2026, 4, 10, 13, 15, tzinfo=UTC)
    _seed_canonical_utterances(
        session_factory,
        utterances=[
            {
                "id": "utt-1",
                "text": "ready to go",
                "speaker_name": "A",
                "started_at": start,
                "ended_at": start + timedelta(seconds=3),
            }
        ],
    )

    helper_calls = {
        "load_room_provenance_slices": 0,
        "build_room_window_audio": 0,
        "resolve_track_identities": 0,
        "align_retranscribed_segments": 0,
    }

    monkeypatch.setattr(
        room_enrichment,
        "load_room_provenance_slices",
        lambda *args, **kwargs: helper_calls.__setitem__(
            "load_room_provenance_slices",
            helper_calls["load_room_provenance_slices"] + 1,
        ) or (FakeProvenanceSlice(canonical_utterance_id="utt-1", raw_track_label="A"),),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "build_room_window_audio",
        lambda *args, **kwargs: helper_calls.__setitem__(
            "build_room_window_audio",
            helper_calls["build_room_window_audio"] + 1,
        ) or FakeRoomWindowAudio(
            audio_bytes=b"window-audio",
            track_bundles=(FakeTrackBundle(raw_track_label="A", audio_bytes=b"track-a", speech_seconds=12.0),),
        ),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "resolve_track_identities",
        lambda *args, **kwargs: helper_calls.__setitem__(
            "resolve_track_identities",
            helper_calls["resolve_track_identities"] + 1,
        ) or (
            FakeResolvedTrackIdentity(
                raw_track_label="A",
                resolved_identity="Dylan",
                identity_method="pyannote-teammate",
                top_match_label="Dylan",
                top_match_confidence=0.99,
                second_match_label="Alex",
                second_match_confidence=0.31,
            ),
        ),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "align_retranscribed_segments",
        lambda *args, **kwargs: helper_calls.__setitem__(
            "align_retranscribed_segments",
            helper_calls["align_retranscribed_segments"] + 1,
        ) or [
            FakeAlignedTranscriptRow(
                canonical_utterance_id="utt-1",
                text="Ready to go.",
                confidence=0.91,
                used_raw_fallback=False,
            )
        ],
        raising=False,
    )

    retranscription_client = FakeRetranscriptionClient(segments=("segment",))
    first = room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        source_id="room-1",
        hours=4,
        resolver_version="room-v2-audio-identity-v1",
        dry_run=False,
        settings=_room_v2_settings(),
        now=lambda: datetime(2026, 4, 10, 15, 0, tzinfo=UTC),
        s3_client=object(),
        pyannote_client=object(),
        retranscription_client=retranscription_client,
        voiceprints=(VoiceprintReference(label="Dylan", voiceprint="vp-dylan"),),
    )
    second = room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        source_id="room-1",
        hours=4,
        resolver_version="room-v2-audio-identity-v1",
        dry_run=False,
        settings=_room_v2_settings(),
        now=lambda: datetime(2026, 4, 10, 15, 0, tzinfo=UTC),
        s3_client=object(),
        pyannote_client=object(),
        retranscription_client=retranscription_client,
        voiceprints=(VoiceprintReference(label="Dylan", voiceprint="vp-dylan"),),
    )

    session = session_factory()
    try:
        enrichment_rows = session.scalars(select(enrichment_model)).all()
    finally:
        session.close()

    assert first.created == 1
    assert second.created == 0
    assert second.utterances == 0
    assert len(enrichment_rows) == 1
    assert retranscription_client.calls == [
        {
            "audio_bytes": b"window-audio",
            "filename": "room-1-20260410T131500Z.wav",
            "window_started_at": start,
            "content_type": "audio/wav",
        }
    ]
    assert helper_calls == {
        "load_room_provenance_slices": 1,
        "build_room_window_audio": 1,
        "resolve_track_identities": 1,
        "align_retranscribed_segments": 1,
    }


def test_room_enrichment_preserves_original_raw_label_when_new_resolver_version_rewrites_canonical(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room_enrichment = _import_room_enrichment_module()
    enrichment_model = _canonical_enrichment_model()
    start = datetime(2026, 4, 10, 13, 15, tzinfo=UTC)
    _seed_canonical_utterances(
        session_factory,
        utterances=[
            {
                "id": "utt-1",
                "text": "ready to go",
                "speaker_name": "A",
                "speaker_confidence": 0.64,
                "started_at": start,
                "ended_at": start + timedelta(seconds=3),
            }
        ],
    )

    monkeypatch.setattr(
        room_enrichment,
        "load_room_provenance_slices",
        lambda *args, **kwargs: (FakeProvenanceSlice(canonical_utterance_id="utt-1", raw_track_label="A"),),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "build_room_window_audio",
        lambda *args, **kwargs: FakeRoomWindowAudio(
            audio_bytes=b"window-audio",
            track_bundles=(FakeTrackBundle(raw_track_label="A", audio_bytes=b"track-a", speech_seconds=12.0),),
        ),
        raising=False,
    )

    track_identity_state = {
        "item": FakeResolvedTrackIdentity(
            raw_track_label="A",
            resolved_identity="Dylan",
            identity_method="pyannote-teammate",
            top_match_label="Dylan",
            top_match_confidence=0.99,
            second_match_label="Alex",
            second_match_confidence=0.31,
        )
    }

    monkeypatch.setattr(
        room_enrichment,
        "resolve_track_identities",
        lambda *args, **kwargs: (track_identity_state["item"],),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "align_retranscribed_segments",
        lambda *args, **kwargs: [
            FakeAlignedTranscriptRow(
                canonical_utterance_id="utt-1",
                text="Ready to go.",
                confidence=0.91,
                used_raw_fallback=False,
            )
        ],
        raising=False,
    )

    retranscription_client = FakeRetranscriptionClient(segments=("segment",))
    room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        source_id="room-1",
        hours=4,
        resolver_version="room-v2-audio-identity-v1",
        dry_run=False,
        settings=_room_v2_settings(),
        now=lambda: datetime(2026, 4, 10, 15, 0, tzinfo=UTC),
        s3_client=object(),
        pyannote_client=object(),
        retranscription_client=retranscription_client,
        voiceprints=(VoiceprintReference(label="Dylan", voiceprint="vp-dylan"),),
    )

    track_identity_state["item"] = FakeResolvedTrackIdentity(
        raw_track_label="A",
        resolved_identity="Niyant",
        identity_method="pyannote-teammate",
        top_match_label="Niyant",
        top_match_confidence=0.88,
        second_match_label="Dylan",
        second_match_confidence=0.51,
    )

    room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        source_id="room-1",
        hours=4,
        resolver_version="room-v2-audio-identity-v2",
        dry_run=False,
        settings=_room_v2_settings(),
        now=lambda: datetime(2026, 4, 10, 15, 0, tzinfo=UTC),
        s3_client=object(),
        pyannote_client=object(),
        retranscription_client=retranscription_client,
        voiceprints=(VoiceprintReference(label="Niyant", voiceprint="vp-niyant"),),
    )

    session = session_factory()
    try:
        canonical_row = session.get(CanonicalUtterance, "utt-1")
        enrichment_rows = session.scalars(
            select(enrichment_model).order_by(enrichment_model.resolver_version)
        ).all()
    finally:
        session.close()

    assert canonical_row is not None
    assert canonical_row.raw_speaker_name == "A"
    assert canonical_row.raw_speaker_confidence == pytest.approx(0.64)
    assert canonical_row.speaker_name == "Niyant"
    assert canonical_row.speaker_confidence == pytest.approx(0.88)
    assert [row.resolver_version for row in enrichment_rows] == [
        "room-v2-audio-identity-v1",
        "room-v2-audio-identity-v2",
    ]
    assert [row.resolved_speaker_name for row in enrichment_rows] == ["Dylan", "Niyant"]


def test_room_enrichment_backfills_missing_raw_label_from_room_provenance(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room_enrichment = _import_room_enrichment_module()
    start = datetime(2026, 4, 10, 13, 15, tzinfo=UTC)
    _seed_canonical_utterances(
        session_factory,
        utterances=[
            {
                "id": "utt-1",
                "text": "ready to go",
                "speaker_name": None,
                "speaker_confidence": None,
                "started_at": start,
                "ended_at": start + timedelta(seconds=3),
            }
        ],
    )

    monkeypatch.setattr(
        room_enrichment,
        "load_room_provenance_slices",
        lambda *args, **kwargs: (FakeProvenanceSlice(canonical_utterance_id="utt-1", raw_track_label="A"),),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "build_room_window_audio",
        lambda *args, **kwargs: FakeRoomWindowAudio(
            audio_bytes=b"window-audio",
            track_bundles=(FakeTrackBundle(raw_track_label="A", audio_bytes=b"track-a", speech_seconds=12.0),),
        ),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "resolve_track_identities",
        lambda *args, **kwargs: (
            FakeResolvedTrackIdentity(
                raw_track_label="A",
                resolved_identity="Dylan",
                identity_method="pyannote-teammate",
                top_match_label="Dylan",
                top_match_confidence=0.99,
                second_match_label="Alex",
                second_match_confidence=0.31,
            ),
        ),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "align_retranscribed_segments",
        lambda *args, **kwargs: [
            FakeAlignedTranscriptRow(
                canonical_utterance_id="utt-1",
                text="Ready to go.",
                confidence=0.91,
                used_raw_fallback=False,
            )
        ],
        raising=False,
    )

    room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        source_id="room-1",
        hours=4,
        resolver_version="room-v2-audio-identity-v1",
        dry_run=False,
        settings=_room_v2_settings(),
        now=lambda: datetime(2026, 4, 10, 15, 0, tzinfo=UTC),
        s3_client=object(),
        pyannote_client=object(),
        retranscription_client=FakeRetranscriptionClient(segments=("segment",)),
        voiceprints=(VoiceprintReference(label="Dylan", voiceprint="vp-dylan"),),
    )

    session = session_factory()
    try:
        canonical_row = session.get(CanonicalUtterance, "utt-1")
    finally:
        session.close()

    assert canonical_row is not None
    assert canonical_row.raw_speaker_name == "A"
    assert canonical_row.raw_speaker_confidence is None
    assert canonical_row.speaker_name == "Dylan"
    assert canonical_row.speaker_confidence == pytest.approx(0.99)


def test_room_enrichment_falls_back_to_raw_track_labels_when_pyannote_identity_fails(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room_enrichment = _import_room_enrichment_module()
    enrichment_model = _canonical_enrichment_model()

    window_start = datetime(2026, 4, 10, 14, 0, tzinfo=UTC)
    _seed_canonical_utterances(
        session_factory,
        utterances=[
            {
                "id": "utt-1",
                "text": "we should ship it after lunch",
                "speaker_name": "A",
                "started_at": window_start + timedelta(minutes=2),
                "ended_at": window_start + timedelta(minutes=2, seconds=5),
            },
            {
                "id": "utt-2",
                "text": "i can take the follow up",
                "speaker_name": "B",
                "started_at": window_start + timedelta(minutes=4),
                "ended_at": window_start + timedelta(minutes=4, seconds=5),
            },
        ],
    )

    provenance_slices = (
        FakeProvenanceSlice(canonical_utterance_id="utt-1", raw_track_label="A"),
        FakeProvenanceSlice(canonical_utterance_id="utt-2", raw_track_label="B"),
    )
    window_audio = FakeRoomWindowAudio(
        audio_bytes=b"window-audio",
        track_bundles=(
            FakeTrackBundle(raw_track_label="A", audio_bytes=b"track-a", speech_seconds=13.2),
            FakeTrackBundle(raw_track_label="B", audio_bytes=b"track-b", speech_seconds=11.4),
        ),
    )
    aligned_rows = (
        FakeAlignedTranscriptRow(
            canonical_utterance_id="utt-1",
            text="We should ship it after lunch.",
            confidence=0.91,
            used_raw_fallback=False,
        ),
        FakeAlignedTranscriptRow(
            canonical_utterance_id="utt-2",
            text="I can take the follow-up.",
            confidence=0.83,
            used_raw_fallback=False,
        ),
    )
    segments = ("segment-1", "segment-2")
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        room_enrichment,
        "load_room_provenance_slices",
        lambda *args, **kwargs: provenance_slices,
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "build_room_window_audio",
        lambda *args, **kwargs: window_audio,
        raising=False,
    )

    def fake_resolve_track_identities(**kwargs):
        calls["resolve_track_identities"] = kwargs
        raise PyannoteError("pyannote request failed: 402 insufficient credits")

    def fake_align_retranscribed_segments(utterances, segments_value, *, window_started_at):
        calls["align_retranscribed_segments"] = {
            "utterance_ids": [utterance.canonical_utterance_id for utterance in utterances],
            "segments": tuple(segments_value),
            "window_started_at": window_started_at,
        }
        return list(aligned_rows)

    monkeypatch.setattr(
        room_enrichment,
        "resolve_track_identities",
        fake_resolve_track_identities,
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "align_retranscribed_segments",
        fake_align_retranscribed_segments,
        raising=False,
    )

    retranscription_client = FakeRetranscriptionClient(segments=segments)

    result = room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        source_id="room-1",
        hours=4,
        resolver_version="room-v2-audio-identity-v1",
        dry_run=False,
        settings=_room_v2_settings(),
        now=lambda: datetime(2026, 4, 10, 16, 0, tzinfo=UTC),
        s3_client=object(),
        pyannote_client=object(),
        retranscription_client=retranscription_client,
        voiceprints=(VoiceprintReference(label="Dylan", voiceprint="vp-dylan"),),
    )

    session = session_factory()
    try:
        enrichment_rows = session.scalars(
            select(enrichment_model).order_by(enrichment_model.canonical_utterance_id)
        ).all()
    finally:
        session.close()

    assert result.created == 2
    assert calls["align_retranscribed_segments"] == {
        "utterance_ids": ["utt-1", "utt-2"],
        "segments": segments,
        "window_started_at": window_start,
    }
    assert retranscription_client.calls == [
        {
            "audio_bytes": b"window-audio",
            "filename": "room-1-20260410T140000Z.wav",
            "window_started_at": window_start,
            "content_type": "audio/wav",
        }
    ]

    persisted = {
        row.canonical_utterance_id: row
        for row in enrichment_rows
    }
    assert persisted["utt-1"].resolved_speaker_name == "A"
    assert persisted["utt-2"].resolved_speaker_name == "B"
    assert persisted["utt-1"].resolved_speaker_confidence is None
    assert persisted["utt-2"].resolved_speaker_confidence is None
    assert persisted["utt-1"].identity_method == "raw-track-fallback"
    assert persisted["utt-2"].identity_method == "raw-track-fallback"
    assert persisted["utt-1"].identity_track_label == "A"
    assert persisted["utt-2"].identity_track_label == "B"
    assert persisted["utt-1"].identity_match_label is None
    assert persisted["utt-2"].identity_match_label is None
    assert persisted["utt-1"].cleaned_text == "We should ship it after lunch."
    assert persisted["utt-2"].cleaned_text == "I can take the follow-up."
    assert persisted["utt-1"].transcript_method == "openai-retranscribe"
    assert persisted["utt-2"].transcript_method == "openai-retranscribe"
    assert persisted["utt-1"].resolution_notes == "speaker identity fell back to raw track label after pyannote failure"
    assert persisted["utt-2"].resolution_notes == "speaker identity fell back to raw track label after pyannote failure"


def test_room_enrichment_dry_run_scopes_to_recent_hours_and_fixed_windows(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room_enrichment = _import_room_enrichment_module()
    enrichment_model = _canonical_enrichment_model()

    now = datetime(2026, 4, 10, 16, 30, tzinfo=UTC)
    _seed_canonical_utterances(
        session_factory,
        utterances=[
            {
                "id": "utt-old",
                "text": "exclude me",
                "speaker_name": "A",
                "started_at": now - timedelta(hours=4, minutes=1),
                "ended_at": now - timedelta(hours=4, minutes=1) + timedelta(seconds=4),
            },
            {
                "id": "utt-1",
                "text": "keep me one",
                "speaker_name": "A",
                "started_at": now - timedelta(hours=3, minutes=59),
                "ended_at": now - timedelta(hours=3, minutes=59) + timedelta(seconds=4),
            },
            {
                "id": "utt-2",
                "text": "keep me two",
                "speaker_name": "B",
                "started_at": now - timedelta(hours=3, minutes=46),
                "ended_at": now - timedelta(hours=3, minutes=46) + timedelta(seconds=4),
            },
            {
                "id": "utt-3",
                "text": "keep me three",
                "speaker_name": None,
                "started_at": now - timedelta(hours=3, minutes=44),
                "ended_at": now - timedelta(hours=3, minutes=44) + timedelta(seconds=4),
            },
        ],
    )
    _seed_canonical_utterances(
        session_factory,
        source_id="desk-a",
        utterances=[
            {
                "id": "desk-1",
                "text": "wrong source",
                "speaker_name": "Dylan",
                "started_at": now - timedelta(minutes=30),
                "ended_at": now - timedelta(minutes=30) + timedelta(seconds=5),
            }
        ],
    )

    monkeypatch.setattr(
        room_enrichment,
        "load_room_provenance_slices",
        lambda *args, **kwargs: pytest.fail("dry-run should not load provenance"),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "build_room_window_audio",
        lambda *args, **kwargs: pytest.fail("dry-run should not build room audio"),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "resolve_track_identities",
        lambda *args, **kwargs: pytest.fail("dry-run should not resolve track identities"),
        raising=False,
    )
    monkeypatch.setattr(
        room_enrichment,
        "align_retranscribed_segments",
        lambda *args, **kwargs: pytest.fail("dry-run should not align retranscribed segments"),
        raising=False,
    )

    result = room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        source_id="room-1",
        hours=4,
        resolver_version="room-v2-audio-identity-v1",
        dry_run=True,
        settings=_room_v2_settings(),
        now=lambda: now,
        s3_client=object(),
        pyannote_client=object(),
        retranscription_client=FakeRetranscriptionClient(segments=()),
        voiceprints=(),
    )

    session = session_factory()
    try:
        enrichment_rows = session.scalars(select(enrichment_model)).all()
    finally:
        session.close()

    assert result.dry_run is True
    assert result.windows == 2
    assert result.utterances == 3
    assert result.created == 0
    assert enrichment_rows == []
