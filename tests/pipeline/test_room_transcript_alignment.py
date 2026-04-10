from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib

import pytest


def _import_client_module():
    try:
        return importlib.import_module("ambient_memory.integrations.openai_room_retranscription_client")
    except ModuleNotFoundError as exc:
        pytest.fail(f"OpenAI room retranscription client module missing: {exc}")


def _import_alignment_module():
    try:
        return importlib.import_module("ambient_memory.pipeline.room_transcript_alignment")
    except ModuleNotFoundError as exc:
        pytest.fail(f"room transcript alignment module missing: {exc}")


def _at(offset_seconds: float) -> datetime:
    return datetime(2026, 4, 10, 13, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)


def test_align_retranscribed_segments_maps_rows_by_max_overlap_and_preserves_order() -> None:
    client_module = _import_client_module()
    alignment_module = _import_alignment_module()
    utterances = (
        alignment_module.CanonicalUtteranceWindowRow(
            canonical_utterance_id="utt-1",
            started_at=_at(0.0),
            ended_at=_at(2.0),
            raw_text="ship it after lunch",
        ),
        alignment_module.CanonicalUtteranceWindowRow(
            canonical_utterance_id="utt-2",
            started_at=_at(2.0),
            ended_at=_at(4.0),
            raw_text="i can take the follow up",
        ),
        alignment_module.CanonicalUtteranceWindowRow(
            canonical_utterance_id="utt-3",
            started_at=_at(4.0),
            ended_at=_at(6.0),
            raw_text="leave the raw text here",
        ),
    )
    segments = (
        client_module.RoomRetranscribedSegment(
            start_seconds=2.10,
            end_seconds=3.40,
            text="I can take the follow-up.",
            confidence=0.81,
        ),
        client_module.RoomRetranscribedSegment(
            start_seconds=0.25,
            end_seconds=1.75,
            text="Ship it after lunch.",
            confidence=0.93,
        ),
        client_module.RoomRetranscribedSegment(
            start_seconds=9.00,
            end_seconds=10.00,
            text="outside the window",
            confidence=0.12,
        ),
    )

    rows = alignment_module.align_retranscribed_segments(
        utterances,
        segments,
        window_started_at=_at(0.0),
    )

    assert [row.canonical_utterance_id for row in rows] == ["utt-1", "utt-2", "utt-3"]
    assert [row.text for row in rows] == [
        "Ship it after lunch.",
        "I can take the follow-up.",
        "leave the raw text here",
    ]
    assert [row.used_raw_fallback for row in rows] == [False, False, True]
    assert [row.confidence for row in rows] == [0.93, 0.81, None]


def test_align_retranscribed_segments_falls_back_to_raw_text_when_overlap_is_ambiguous() -> None:
    client_module = _import_client_module()
    alignment_module = _import_alignment_module()
    utterances = (
        alignment_module.CanonicalUtteranceWindowRow(
            canonical_utterance_id="utt-1",
            started_at=_at(0.0),
            ended_at=_at(2.0),
            raw_text="raw turn one",
        ),
    )
    segments = (
        client_module.RoomRetranscribedSegment(
            start_seconds=0.0,
            end_seconds=1.0,
            text="first half",
            confidence=0.61,
        ),
        client_module.RoomRetranscribedSegment(
            start_seconds=1.0,
            end_seconds=2.0,
            text="second half",
            confidence=0.64,
        ),
    )

    rows = alignment_module.align_retranscribed_segments(
        utterances,
        segments,
        window_started_at=_at(0.0),
    )

    assert len(rows) == 1
    assert rows[0].canonical_utterance_id == "utt-1"
    assert rows[0].text == "raw turn one"
    assert rows[0].used_raw_fallback is True
    assert rows[0].confidence is None
