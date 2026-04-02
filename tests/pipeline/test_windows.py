from datetime import UTC, datetime

from ambient_memory.pipeline.windows import WindowChunk, group_processing_windows


def test_group_processing_windows_merges_overlapping_and_near_adjacent_chunks() -> None:
    chunks = [
        WindowChunk(
            chunk_id="chunk-1",
            source_id="desk-a",
            started_at=datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC),
        ),
        WindowChunk(
            chunk_id="chunk-2",
            source_id="room-1",
            started_at=datetime(2026, 4, 2, 13, 0, 18, tzinfo=UTC),
            ended_at=datetime(2026, 4, 2, 13, 0, 42, tzinfo=UTC),
        ),
        WindowChunk(
            chunk_id="chunk-3",
            source_id="desk-a",
            started_at=datetime(2026, 4, 2, 13, 0, 45, tzinfo=UTC),
            ended_at=datetime(2026, 4, 2, 13, 1, 0, tzinfo=UTC),
        ),
        WindowChunk(
            chunk_id="chunk-4",
            source_id="room-1",
            started_at=datetime(2026, 4, 2, 13, 1, 10, tzinfo=UTC),
            ended_at=datetime(2026, 4, 2, 13, 1, 40, tzinfo=UTC),
        ),
    ]

    windows = group_processing_windows(chunks, adjacency_seconds=5, max_window_seconds=60)

    assert len(windows) == 2

    first, second = windows
    assert [chunk.chunk_id for chunk in first.chunks] == ["chunk-1", "chunk-2", "chunk-3"]
    assert first.started_at == datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC)
    assert first.ended_at == datetime(2026, 4, 2, 13, 1, 0, tzinfo=UTC)
    assert set(first.source_ids) == {"desk-a", "room-1"}

    assert [chunk.chunk_id for chunk in second.chunks] == ["chunk-4"]
    assert second.started_at == datetime(2026, 4, 2, 13, 1, 10, tzinfo=UTC)
    assert second.ended_at == datetime(2026, 4, 2, 13, 1, 40, tzinfo=UTC)
