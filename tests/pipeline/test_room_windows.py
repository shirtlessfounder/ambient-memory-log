from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ambient_memory.pipeline.room_windows import PendingRoomChunk, select_room_windows


def _chunk(
    chunk_id: str,
    *,
    source_id: str,
    start_offset_seconds: int,
    duration_seconds: int = 30,
) -> PendingRoomChunk:
    started_at = datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC) + timedelta(seconds=start_offset_seconds)
    return PendingRoomChunk(
        chunk_id=chunk_id,
        source_id=source_id,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=duration_seconds),
    )


def test_select_room_windows_groups_contiguous_room_chunks_into_fixed_windows() -> None:
    chunks = [
        _chunk(f"room-{index:02d}", source_id="room-1", start_offset_seconds=index * 30)
        for index in range(22)
    ]

    selection = select_room_windows(
        chunks,
        window_seconds=600,
        idle_flush_seconds=120,
        now=datetime(2026, 4, 2, 13, 11, 30, tzinfo=UTC),
    )

    assert len(selection.ready_batches) == 1
    assert [chunk.chunk_id for chunk in selection.ready_batches[0].chunks] == [f"room-{index:02d}" for index in range(20)]
    assert selection.ready_batches[0].started_at == datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC)
    assert selection.ready_batches[0].ended_at == datetime(2026, 4, 2, 13, 10, 0, tzinfo=UTC)
    assert [chunk.chunk_id for chunk in selection.pending_chunks] == ["room-20", "room-21"]


def test_select_room_windows_ignores_non_room_chunks() -> None:
    chunks = [
        _chunk("desk-1", source_id="desk-a", start_offset_seconds=0),
        _chunk("room-1", source_id="room-1", start_offset_seconds=0),
        _chunk("desk-2", source_id="desk-a", start_offset_seconds=30),
        _chunk("room-2", source_id="room-1", start_offset_seconds=30),
    ]

    selection = select_room_windows(
        chunks,
        window_seconds=60,
        idle_flush_seconds=120,
        now=datetime(2026, 4, 2, 13, 3, 0, tzinfo=UTC),
    )

    assert len(selection.ready_batches) == 1
    assert [chunk.chunk_id for chunk in selection.ready_batches[0].chunks] == ["room-1", "room-2"]
    assert selection.pending_chunks == ()


def test_select_room_windows_leaves_short_room_span_pending_until_idle_flush() -> None:
    chunks = [
        _chunk(f"room-{index:02d}", source_id="room-1", start_offset_seconds=index * 30)
        for index in range(4)
    ]

    selection = select_room_windows(
        chunks,
        window_seconds=600,
        idle_flush_seconds=120,
        now=datetime(2026, 4, 2, 13, 2, 20, tzinfo=UTC),
    )

    assert selection.ready_batches == ()
    assert [chunk.chunk_id for chunk in selection.pending_chunks] == [f"room-{index:02d}" for index in range(4)]


def test_select_room_windows_flushes_short_room_span_after_idle_threshold() -> None:
    chunks = [
        _chunk(f"room-{index:02d}", source_id="room-1", start_offset_seconds=index * 30)
        for index in range(4)
    ]

    selection = select_room_windows(
        chunks,
        window_seconds=600,
        idle_flush_seconds=120,
        now=datetime(2026, 4, 2, 13, 4, 1, tzinfo=UTC),
    )

    assert len(selection.ready_batches) == 1
    assert [chunk.chunk_id for chunk in selection.ready_batches[0].chunks] == [f"room-{index:02d}" for index in range(4)]
    assert selection.pending_chunks == ()


def test_select_room_windows_does_not_bridge_discontiguous_room_spans() -> None:
    chunks = [
        _chunk(f"room-a-{index}", source_id="room-1", start_offset_seconds=index * 30)
        for index in range(10)
    ] + [
        _chunk(f"room-b-{index}", source_id="room-1", start_offset_seconds=600 + (index * 30))
        for index in range(10)
    ]

    selection = select_room_windows(
        chunks,
        window_seconds=600,
        idle_flush_seconds=120,
        now=datetime(2026, 4, 2, 13, 16, 0, tzinfo=UTC),
    )

    assert len(selection.ready_batches) == 1
    assert [chunk.chunk_id for chunk in selection.ready_batches[0].chunks] == [f"room-a-{index}" for index in range(10)]
    assert [chunk.chunk_id for chunk in selection.pending_chunks] == [f"room-b-{index}" for index in range(10)]
