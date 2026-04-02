from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable


DEFAULT_ADJACENCY_SECONDS = 5
DEFAULT_MAX_WINDOW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class WindowChunk:
    chunk_id: str
    source_id: str
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class ProcessingWindow:
    chunks: tuple[WindowChunk, ...]
    started_at: datetime
    ended_at: datetime
    source_ids: tuple[str, ...]


def group_processing_windows(
    chunks: Iterable[WindowChunk],
    *,
    adjacency_seconds: int = DEFAULT_ADJACENCY_SECONDS,
    max_window_seconds: int = DEFAULT_MAX_WINDOW_SECONDS,
) -> list[ProcessingWindow]:
    ordered_chunks = sorted(
        chunks,
        key=lambda chunk: (chunk.started_at, chunk.ended_at, chunk.source_id, chunk.chunk_id),
    )
    if not ordered_chunks:
        return []

    adjacency = timedelta(seconds=adjacency_seconds)
    max_window = timedelta(seconds=max_window_seconds)
    windows: list[ProcessingWindow] = []

    current_chunks = [ordered_chunks[0]]
    current_started_at = ordered_chunks[0].started_at
    current_ended_at = ordered_chunks[0].ended_at

    for chunk in ordered_chunks[1:]:
        proposed_ended_at = max(current_ended_at, chunk.ended_at)
        can_merge = (
            chunk.started_at <= current_ended_at + adjacency
            and proposed_ended_at - current_started_at <= max_window
        )
        if can_merge:
            current_chunks.append(chunk)
            current_ended_at = proposed_ended_at
            continue

        windows.append(
            _build_processing_window(
                current_chunks,
                started_at=current_started_at,
                ended_at=current_ended_at,
            )
        )
        current_chunks = [chunk]
        current_started_at = chunk.started_at
        current_ended_at = chunk.ended_at

    windows.append(
        _build_processing_window(
            current_chunks,
            started_at=current_started_at,
            ended_at=current_ended_at,
        )
    )
    return windows


def _build_processing_window(
    chunks: list[WindowChunk],
    *,
    started_at: datetime,
    ended_at: datetime,
) -> ProcessingWindow:
    source_ids = tuple(sorted({chunk.source_id for chunk in chunks}))
    return ProcessingWindow(
        chunks=tuple(chunks),
        started_at=started_at,
        ended_at=ended_at,
        source_ids=source_ids,
    )
