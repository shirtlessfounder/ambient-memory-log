from __future__ import annotations

from datetime import UTC, datetime
import importlib
import os
from pathlib import Path
from typing import Any

import pytest


def load_spool_module() -> Any:
    try:
        module = importlib.import_module("ambient_memory.capture.spool")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing spool module: {exc}")

    for name in ("LocalSpool", "SpoolBacklogFullError", "SpoolEntry"):
        if not hasattr(module, name):
            pytest.fail(f"missing spool symbol: {name}")

    return module


def write_chunk(path: Path, *, age_seconds: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio-bytes")
    timestamp = datetime.now(UTC).timestamp() - age_seconds
    os.utime(path, (timestamp, timestamp))
    return path


def test_local_spool_skips_recent_capture_chunks(tmp_path: Path) -> None:
    spool_module = load_spool_module()
    spool = spool_module.LocalSpool(tmp_path, settle_seconds=5)

    write_chunk(tmp_path / "chunk-session-20260402T090000.wav", age_seconds=1)

    entries = spool.iter_ready()

    assert entries == []


def test_local_spool_moves_failed_chunks_into_retry_queue(tmp_path: Path) -> None:
    spool_module = load_spool_module()
    spool = spool_module.LocalSpool(tmp_path, settle_seconds=0)
    chunk_path = write_chunk(tmp_path / "chunk-session-20260402T090000.wav", age_seconds=10)

    entry = spool.iter_ready()[0]
    failed_entry = spool.mark_failed(entry, "network down")

    assert not chunk_path.exists()
    assert failed_entry.path.parent == tmp_path / "retry"
    assert failed_entry.path.exists()
    assert failed_entry.attempts == 1
    assert failed_entry.last_error == "network down"
    assert spool.iter_ready()[0].attempts == 1


def test_local_spool_removes_uploaded_chunks_and_sidecars(tmp_path: Path) -> None:
    spool_module = load_spool_module()
    spool = spool_module.LocalSpool(tmp_path, settle_seconds=0)

    chunk_path = write_chunk(tmp_path / "chunk-session-20260402T090000.wav", age_seconds=10)
    entry = spool.mark_failed(spool.iter_ready()[0], "network down")

    spool.mark_uploaded(entry)

    assert not entry.path.exists()
    assert not chunk_path.exists()
    assert spool.iter_ready() == []


def test_local_spool_raises_when_retry_backlog_is_full(tmp_path: Path) -> None:
    spool_module = load_spool_module()
    spool = spool_module.LocalSpool(tmp_path, settle_seconds=0, max_backlog_files=1)

    first_chunk = write_chunk(tmp_path / "chunk-session-20260402T090000.wav", age_seconds=10)
    second_chunk = write_chunk(tmp_path / "chunk-session-20260402T090030.wav", age_seconds=10)

    spool.mark_failed(spool_module.SpoolEntry(path=first_chunk), "network down")

    with pytest.raises(spool_module.SpoolBacklogFullError):
        spool.mark_failed(spool_module.SpoolEntry(path=second_chunk), "still down")

    assert second_chunk.exists()


def test_local_spool_counts_root_and_retry_chunks_toward_capacity(tmp_path: Path) -> None:
    spool_module = load_spool_module()
    spool = spool_module.LocalSpool(tmp_path, settle_seconds=0, max_backlog_files=2)

    write_chunk(tmp_path / "chunk-session-20260402T090000.wav", age_seconds=10)
    write_chunk(tmp_path / "retry" / "chunk-session-20260402T090030.wav", age_seconds=10)

    assert spool.backlog_file_count() == 2
    assert spool.is_backlog_at_capacity() is True


def test_local_spool_defers_new_chunks_when_retry_backlog_is_full(tmp_path: Path) -> None:
    spool_module = load_spool_module()
    spool = spool_module.LocalSpool(tmp_path, settle_seconds=0, max_backlog_files=1)

    retry_chunk = write_chunk(tmp_path / "retry" / "chunk-session-20260402T090000.wav", age_seconds=10)
    write_chunk(tmp_path / "chunk-session-20260402T090030.wav", age_seconds=10)

    entries = spool.iter_ready()

    assert [entry.path for entry in entries] == [retry_chunk]
