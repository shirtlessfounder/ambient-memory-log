from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.models import AudioChunk, CanonicalUtterance, Source, TranscriptCandidate, UtteranceSource
from ambient_memory.pipeline.dedup import DedupCandidate, merge_transcript_candidates, persist_canonical_utterances


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(TSVECTOR, "sqlite")
def _compile_tsvector_sqlite(_type, _compiler, **_kwargs) -> str:
    return "TEXT"


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Source.__table__.create(bind=engine)
    AudioChunk.__table__.create(bind=engine)
    TranscriptCandidate.__table__.create(bind=engine)
    CanonicalUtterance.__table__.create(bind=engine)
    UtteranceSource.__table__.create(bind=engine)

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _at(seconds: float) -> datetime:
    return datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


def _candidate(
    transcript_candidate_id: str,
    *,
    source_id: str,
    text: str,
    start_seconds: float,
    end_seconds: float,
    source_owner: str | None = None,
    speaker_name: str | None = None,
    speaker_confidence: float | None = 0.8,
    confidence: float | None = 0.8,
) -> DedupCandidate:
    return DedupCandidate(
        transcript_candidate_id=transcript_candidate_id,
        source_id=source_id,
        source_owner=source_owner,
        text=text,
        started_at=_at(start_seconds),
        ended_at=_at(end_seconds),
        speaker_name=speaker_name,
        speaker_confidence=speaker_confidence,
        confidence=confidence,
    )


def test_persist_canonical_utterances_prefers_stronger_local_source_candidate(session: Session) -> None:
    local_source = Source(id="desk-a", source_type="macbook", device_owner="Dylan")
    room_source = Source(id="room-1", source_type="room", device_owner=None)
    session.add_all([local_source, room_source])

    local_chunk = AudioChunk(
        id="chunk-local",
        source_id="desk-a",
        s3_bucket="ambient-memory",
        s3_key="raw-audio/desk-a/chunk-local.wav",
        status="uploaded",
        started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
    )
    room_chunk = AudioChunk(
        id="chunk-room",
        source_id="room-1",
        s3_bucket="ambient-memory",
        s3_key="raw-audio/room-1/chunk-room.wav",
        status="uploaded",
        started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
    )
    session.add_all([local_chunk, room_chunk])

    local_candidate_row = TranscriptCandidate(
        id="candidate-local",
        audio_chunk_id=local_chunk.id,
        source_id="desk-a",
        vendor="deepgram",
        vendor_segment_id="utt-local",
        text="Hello there.",
        speaker_hint="speaker_0",
        speaker_confidence=0.92,
        confidence=0.94,
        started_at=datetime(2026, 4, 2, 13, 0, 1, tzinfo=UTC),
        ended_at=datetime(2026, 4, 2, 13, 0, 4, tzinfo=UTC),
        raw_payload={"segment": "local"},
    )
    room_candidate_row = TranscriptCandidate(
        id="candidate-room",
        audio_chunk_id=room_chunk.id,
        source_id="room-1",
        vendor="deepgram",
        vendor_segment_id="utt-room",
        text="hello there",
        speaker_hint="speaker_0",
        speaker_confidence=0.8,
        confidence=0.84,
        started_at=datetime(2026, 4, 2, 13, 0, 1, 500000, tzinfo=UTC),
        ended_at=datetime(2026, 4, 2, 13, 0, 4, 500000, tzinfo=UTC),
        raw_payload={"segment": "room"},
    )
    session.add_all([local_candidate_row, room_candidate_row])
    session.flush()

    merged = merge_transcript_candidates(
        [
            DedupCandidate(
                transcript_candidate_id=local_candidate_row.id,
                source_id=local_candidate_row.source_id,
                source_owner=local_source.device_owner,
                text=local_candidate_row.text,
                started_at=local_candidate_row.started_at,
                ended_at=local_candidate_row.ended_at,
                speaker_name="Dylan",
                speaker_confidence=local_candidate_row.speaker_confidence,
                confidence=local_candidate_row.confidence,
            ),
            DedupCandidate(
                transcript_candidate_id=room_candidate_row.id,
                source_id=room_candidate_row.source_id,
                source_owner=room_source.device_owner,
                text=room_candidate_row.text,
                started_at=room_candidate_row.started_at,
                ended_at=room_candidate_row.ended_at,
                speaker_name=None,
                speaker_confidence=room_candidate_row.speaker_confidence,
                confidence=room_candidate_row.confidence,
            ),
        ]
    )
    persisted = persist_canonical_utterances(session, merged)
    session.commit()

    stored = session.scalars(select(CanonicalUtterance)).all()
    provenance = session.scalars(
        select(UtteranceSource).order_by(UtteranceSource.transcript_candidate_id)
    ).all()

    assert len(merged) == 1
    assert len(persisted) == 1
    assert len(stored) == 1
    assert stored[0].canonical_source_id == "desk-a"
    assert stored[0].text == "Hello there."
    assert stored[0].speaker_name == "Dylan"
    assert stored[0].speaker_confidence == pytest.approx(0.92)
    assert len(provenance) == 2
    assert {row.transcript_candidate_id for row in provenance} == {"candidate-local", "candidate-room"}
    assert sum(row.is_canonical for row in provenance) == 1


def test_merge_transcript_candidates_merges_exact_normalized_text_within_five_seconds() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                source_owner="Dylan",
                text="We should ship it.",
                start_seconds=1.0,
                end_seconds=3.0,
                speaker_name="Dylan",
                speaker_confidence=0.9,
                confidence=0.94,
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="we should ship it",
                start_seconds=7.0,
                end_seconds=9.0,
                confidence=0.84,
            ),
        ]
    )

    assert len(merged) == 1
    assert merged[0].canonical_candidate_id == "candidate-a"
    assert merged[0].transcript_candidate_ids == ("candidate-a", "candidate-b")


def test_merge_transcript_candidates_does_not_merge_exact_text_for_conflicting_named_speakers() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                source_owner="Dylan",
                text="We should ship it.",
                start_seconds=1.0,
                end_seconds=3.0,
                speaker_name="Dylan",
                speaker_confidence=0.9,
                confidence=0.94,
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="we should ship it",
                start_seconds=7.0,
                end_seconds=9.0,
                speaker_name="Alex",
                speaker_confidence=0.86,
                confidence=0.84,
            ),
        ]
    )

    assert len(merged) == 2


def test_merge_transcript_candidates_does_not_bridge_conflicting_named_speakers_through_unnamed_candidate() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-dylan",
                source_id="desk-a",
                source_owner="Dylan",
                text="We should ship it.",
                start_seconds=1.0,
                end_seconds=3.0,
                speaker_name="Dylan",
                speaker_confidence=0.9,
                confidence=0.94,
            ),
            _candidate(
                "candidate-unnamed",
                source_id="room-1",
                text="we should ship it",
                start_seconds=1.2,
                end_seconds=3.2,
                confidence=0.84,
            ),
            _candidate(
                "candidate-alex",
                source_id="room-2",
                text="we should ship it",
                start_seconds=1.4,
                end_seconds=3.4,
                speaker_name="Alex",
                speaker_confidence=0.86,
                confidence=0.85,
            ),
        ]
    )

    assert len(merged) == 2
    assert merged[0].transcript_candidate_ids == ("candidate-dylan", "candidate-unnamed")
    assert merged[1].transcript_candidate_ids == ("candidate-alex",)


def test_merge_transcript_candidates_fuzzy_merges_cross_source_contained_fragment() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                source_owner="Dylan",
                text="ship the migration after lunch",
                start_seconds=1.0,
                end_seconds=4.0,
                speaker_name="Dylan",
                speaker_confidence=0.92,
                confidence=0.95,
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="I think we should ship the migration after lunch today",
                start_seconds=2.0,
                end_seconds=5.5,
                speaker_name="Dylan",
                speaker_confidence=0.75,
                confidence=0.84,
            ),
        ]
    )

    assert len(merged) == 1
    assert merged[0].transcript_candidate_ids == ("candidate-a", "candidate-b")


def test_merge_transcript_candidates_does_not_merge_same_time_different_content() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                text="We should ship the migration after lunch.",
                start_seconds=1.0,
                end_seconds=4.0,
                source_owner="Dylan",
                speaker_name="Dylan",
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="We should postpone the migration until next week.",
                start_seconds=1.5,
                end_seconds=4.5,
                speaker_name="Dylan",
            ),
        ]
    )

    assert len(merged) == 2


def test_merge_transcript_candidates_does_not_merge_same_source_exact_repeat_within_five_seconds() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                source_owner="Dylan",
                text="We should ship it.",
                start_seconds=1.0,
                end_seconds=3.0,
                speaker_name="Dylan",
                speaker_confidence=0.9,
                confidence=0.94,
            ),
            _candidate(
                "candidate-b",
                source_id="desk-a",
                source_owner="Dylan",
                text="we should ship it",
                start_seconds=7.0,
                end_seconds=9.0,
                speaker_name="Dylan",
                speaker_confidence=0.88,
                confidence=0.9,
            ),
        ]
    )

    assert len(merged) == 2


def test_merge_transcript_candidates_does_not_fuzzy_merge_negated_variant() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                text="we should ship the migration after lunch",
                start_seconds=1.0,
                end_seconds=4.0,
                source_owner="Dylan",
                speaker_name="Dylan",
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="we should not ship the migration after lunch",
                start_seconds=1.5,
                end_seconds=4.5,
                speaker_name="Dylan",
            ),
        ]
    )

    assert len(merged) == 2


def test_merge_transcript_candidates_does_not_fuzzy_merge_contraction_negation_variant() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                text="I can approve the deploy",
                start_seconds=1.0,
                end_seconds=4.0,
                source_owner="Dylan",
                speaker_name="Dylan",
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="I can't approve the deploy",
                start_seconds=1.5,
                end_seconds=4.5,
                speaker_name="Dylan",
            ),
        ]
    )

    assert len(merged) == 2


def test_merge_transcript_candidates_does_not_fuzzy_merge_key_token_replacement() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                text="we should ship the migration after lunch because finance approved it",
                start_seconds=1.0,
                end_seconds=4.0,
                source_owner="Dylan",
                speaker_name="Dylan",
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="we should delay the migration after lunch because finance approved it",
                start_seconds=1.5,
                end_seconds=4.5,
                speaker_name="Dylan",
            ),
        ]
    )

    assert len(merged) == 2


def test_merge_transcript_candidates_does_not_fuzzy_merge_possessive_pronoun_variant() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                text="my deployment is broken again today",
                start_seconds=1.0,
                end_seconds=4.0,
                source_owner="Dylan",
                speaker_name="Dylan",
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="your deployment is broken again today",
                start_seconds=1.5,
                end_seconds=4.5,
                speaker_name="Dylan",
            ),
        ]
    )

    assert len(merged) == 2


def test_merge_transcript_candidates_does_not_over_merge_low_signal_acknowledgements() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                text="Yeah.",
                start_seconds=1.0,
                end_seconds=1.5,
                source_owner="Dylan",
                speaker_name="Dylan",
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="yeah",
                start_seconds=5.0,
                end_seconds=5.5,
            ),
        ]
    )

    assert len(merged) == 2


def test_merge_transcript_candidates_blocks_fuzzy_merge_for_conflicting_named_speakers() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                source_owner="Dylan",
                text="ship the migration after lunch",
                start_seconds=1.0,
                end_seconds=4.0,
                speaker_name="Dylan",
                speaker_confidence=0.9,
                confidence=0.95,
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="I think we should ship the migration after lunch today",
                start_seconds=2.0,
                end_seconds=5.0,
                speaker_name="Alex",
                speaker_confidence=0.86,
                confidence=0.85,
            ),
        ]
    )

    assert len(merged) == 2


def test_merge_transcript_candidates_keeps_canonical_candidate_preference_for_fuzzy_merge() -> None:
    merged = merge_transcript_candidates(
        [
            _candidate(
                "candidate-a",
                source_id="desk-a",
                source_owner="Dylan",
                text="ship the migration after lunch",
                start_seconds=1.0,
                end_seconds=4.0,
                speaker_name="Dylan",
                speaker_confidence=0.92,
                confidence=0.95,
            ),
            _candidate(
                "candidate-b",
                source_id="room-1",
                text="I think we should ship the migration after lunch today",
                start_seconds=2.0,
                end_seconds=5.5,
                speaker_name="Dylan",
                speaker_confidence=0.7,
                confidence=0.84,
            ),
        ]
    )

    assert len(merged) == 1
    assert merged[0].canonical_candidate_id == "candidate-a"
    assert merged[0].canonical_source_id == "desk-a"
    assert merged[0].text == "ship the migration after lunch"
