from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

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
                    started_at=utterance["started_at"],
                    ended_at=utterance["ended_at"],
                    processing_version="v1",
                )
            )
        session.commit()
    finally:
        session.close()


def test_room_enrichment_persists_separately_preserves_raw_fields_and_allows_unknown(
    session_factory: sessionmaker[Session],
) -> None:
    room_enrichment = _import_room_enrichment_module()
    enrichment_model = _canonical_enrichment_model()

    class FakeResolver:
        vendor = "openai"

        def __init__(self) -> None:
            self.speaker_calls: list[list[str]] = []
            self.cleanup_calls: list[list[str]] = []

        def resolve_speakers(self, utterances, *, allowed_speakers):
            assert tuple(allowed_speakers) == ("Dylan", "Niyant", "Alex", "Jakub", "unknown")
            self.speaker_calls.append([utterance.canonical_utterance_id for utterance in utterances])
            return [
                room_enrichment.RoomEnrichmentSpeakerResolution(
                    canonical_utterance_id="utt-1",
                    resolved_speaker_name="Dylan",
                    resolved_speaker_confidence=0.94,
                    resolution_notes="named turn",
                ),
                room_enrichment.RoomEnrichmentSpeakerResolution(
                    canonical_utterance_id="utt-2",
                    resolved_speaker_name="unknown",
                    resolved_speaker_confidence=0.41,
                    resolution_notes="not enough evidence",
                ),
            ]

        def cleanup_text(self, utterances, *, speaker_resolutions):
            self.cleanup_calls.append([utterance.canonical_utterance_id for utterance in utterances])
            assert [resolution.resolved_speaker_name for resolution in speaker_resolutions] == ["Dylan", "unknown"]
            return [
                room_enrichment.RoomEnrichmentTextCleanup(
                    canonical_utterance_id="utt-1",
                    cleaned_text="We should ship it after lunch.",
                    cleaned_text_confidence=0.88,
                ),
                room_enrichment.RoomEnrichmentTextCleanup(
                    canonical_utterance_id="utt-2",
                    cleaned_text="I can take the follow-up.",
                    cleaned_text_confidence=0.74,
                ),
            ]

    start = datetime(2026, 4, 10, 14, 0, tzinfo=UTC)
    _seed_canonical_utterances(
        session_factory,
        utterances=[
            {
                "id": "utt-1",
                "text": "we should ship it after lunch",
                "speaker_name": "A",
                "started_at": start,
                "ended_at": start + timedelta(seconds=6),
            },
            {
                "id": "utt-2",
                "text": "i can take the follow up",
                "speaker_name": None,
                "started_at": start + timedelta(minutes=4),
                "ended_at": start + timedelta(minutes=4, seconds=5),
            },
        ],
    )

    session = session_factory()
    try:
        before_rows = session.scalars(select(CanonicalUtterance).order_by(CanonicalUtterance.started_at)).all()
        before_snapshot = {
            row.id: (row.text, row.speaker_name, row.started_at, row.ended_at)
            for row in before_rows
        }
    finally:
        session.close()

    result = room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        resolver=FakeResolver(),
        source_id="room-1",
        hours=4,
        resolver_version="openai-room-v1",
        dry_run=False,
        now=lambda: datetime(2026, 4, 10, 15, 0, tzinfo=UTC),
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
    assert result.utterances == 2
    assert result.created == 2
    assert [row.id for row in canonical_rows] == ["utt-1", "utt-2"]
    assert [row.canonical_utterance_id for row in enrichment_rows] == ["utt-1", "utt-2"]
    assert [row.resolved_speaker_name for row in enrichment_rows] == ["Dylan", "unknown"]
    assert [row.cleaned_text for row in enrichment_rows] == [
        "We should ship it after lunch.",
        "I can take the follow-up.",
    ]
    assert {
        row.id: (row.text, row.speaker_name, row.started_at, row.ended_at)
        for row in canonical_rows
    } == before_snapshot


def test_room_enrichment_rerun_is_idempotent_for_same_resolver_version(
    session_factory: sessionmaker[Session],
) -> None:
    room_enrichment = _import_room_enrichment_module()
    enrichment_model = _canonical_enrichment_model()

    class FakeResolver:
        vendor = "openai"

        def __init__(self) -> None:
            self.calls = 0

        def resolve_speakers(self, utterances, *, allowed_speakers):
            self.calls += 1
            return [
                room_enrichment.RoomEnrichmentSpeakerResolution(
                    canonical_utterance_id=utterances[0].canonical_utterance_id,
                    resolved_speaker_name="Dylan",
                    resolved_speaker_confidence=0.99,
                    resolution_notes="clear match",
                )
            ]

        def cleanup_text(self, utterances, *, speaker_resolutions):
            return [
                room_enrichment.RoomEnrichmentTextCleanup(
                    canonical_utterance_id=utterances[0].canonical_utterance_id,
                    cleaned_text="Ready to go.",
                    cleaned_text_confidence=0.91,
                )
            ]

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

    resolver = FakeResolver()
    first = room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        resolver=resolver,
        source_id="room-1",
        hours=4,
        resolver_version="openai-room-v1",
        dry_run=False,
        now=lambda: datetime(2026, 4, 10, 15, 0, tzinfo=UTC),
    )
    second = room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        resolver=resolver,
        source_id="room-1",
        hours=4,
        resolver_version="openai-room-v1",
        dry_run=False,
        now=lambda: datetime(2026, 4, 10, 15, 0, tzinfo=UTC),
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
    assert resolver.calls == 1


def test_room_enrichment_dry_run_scopes_to_recent_hours_and_fixed_windows(
    session_factory: sessionmaker[Session],
) -> None:
    room_enrichment = _import_room_enrichment_module()
    enrichment_model = _canonical_enrichment_model()

    class FakeResolver:
        vendor = "openai"

        def resolve_speakers(self, utterances, *, allowed_speakers):
            raise AssertionError("dry-run should not resolve speakers")

        def cleanup_text(self, utterances, *, speaker_resolutions):
            raise AssertionError("dry-run should not clean text")

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

    result = room_enrichment.run_room_enrichment(
        session_factory=session_factory,
        resolver=FakeResolver(),
        source_id="room-1",
        hours=4,
        resolver_version="openai-room-v1",
        dry_run=True,
        now=lambda: now,
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
