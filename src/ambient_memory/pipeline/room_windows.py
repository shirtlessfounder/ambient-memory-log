from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable


ROOM_SOURCE_ID = "room-1"
DEFAULT_ROOM_WINDOW_SECONDS = 600
DEFAULT_ROOM_IDLE_FLUSH_SECONDS = 120
DEFAULT_ROOM_ADJACENCY_SECONDS = 5


@dataclass(frozen=True, slots=True)
class PendingRoomChunk:
    chunk_id: str
    source_id: str
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class RoomWindowBatch:
    chunks: tuple[PendingRoomChunk, ...]
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class RoomWindowSelection:
    ready_batches: tuple[RoomWindowBatch, ...]
    pending_chunks: tuple[PendingRoomChunk, ...]


def select_room_windows(
    chunks: Iterable[PendingRoomChunk],
    *,
    window_seconds: int = DEFAULT_ROOM_WINDOW_SECONDS,
    idle_flush_seconds: int = DEFAULT_ROOM_IDLE_FLUSH_SECONDS,
    now: datetime,
    room_source_id: str = ROOM_SOURCE_ID,
    adjacency_seconds: int = DEFAULT_ROOM_ADJACENCY_SECONDS,
) -> RoomWindowSelection:
    ordered_chunks = sorted(
        (
            chunk
            for chunk in chunks
            if chunk.source_id == room_source_id
        ),
        key=lambda chunk: (chunk.started_at, chunk.ended_at, chunk.chunk_id),
    )
    if not ordered_chunks:
        return RoomWindowSelection(ready_batches=(), pending_chunks=())

    adjacency = timedelta(seconds=adjacency_seconds)
    window = timedelta(seconds=window_seconds)
    idle_flush = timedelta(seconds=idle_flush_seconds)

    ready_batches: list[RoomWindowBatch] = []
    pending_chunks: list[PendingRoomChunk] = []

    for span in _group_contiguous_spans(ordered_chunks, adjacency=adjacency):
        remaining = list(span)
        while remaining:
            batch_chunks: list[PendingRoomChunk] = []
            batch_started_at = remaining[0].started_at
            batch_ended_at = remaining[0].ended_at
            index = 0
            while index < len(remaining) and batch_ended_at - batch_started_at < window:
                chunk = remaining[index]
                batch_chunks.append(chunk)
                batch_ended_at = max(batch_ended_at, chunk.ended_at)
                index += 1

            if batch_ended_at - batch_started_at >= window:
                ready_batches.append(
                    RoomWindowBatch(
                        chunks=tuple(batch_chunks),
                        started_at=batch_started_at,
                        ended_at=batch_ended_at,
                    )
                )
                remaining = remaining[index:]
                continue

            if now - remaining[-1].ended_at >= idle_flush:
                ready_batches.append(
                    RoomWindowBatch(
                        chunks=tuple(remaining),
                        started_at=remaining[0].started_at,
                        ended_at=remaining[-1].ended_at,
                    )
                )
            else:
                pending_chunks.extend(remaining)
            break

    return RoomWindowSelection(
        ready_batches=tuple(ready_batches),
        pending_chunks=tuple(pending_chunks),
    )


def _group_contiguous_spans(
    chunks: list[PendingRoomChunk],
    *,
    adjacency: timedelta,
) -> list[tuple[PendingRoomChunk, ...]]:
    spans: list[list[PendingRoomChunk]] = [[chunks[0]]]
    for chunk in chunks[1:]:
        current_span = spans[-1]
        if chunk.started_at <= current_span[-1].ended_at + adjacency:
            current_span.append(chunk)
            continue
        spans.append([chunk])
    return [tuple(span) for span in spans]
