from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SpoolEntry:
    path: Path
    attempts: int = 0
    last_error: str | None = None


class SpoolBacklogFullError(RuntimeError):
    pass


class LocalSpool:
    def __init__(
        self,
        root: Path | str,
        *,
        settle_seconds: int = 2,
        max_backlog_files: int = 2048,
    ) -> None:
        self.root = Path(root)
        self.retry_dir = self.root / "retry"
        self.settle_seconds = settle_seconds
        self.max_backlog_files = max_backlog_files

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.retry_dir.mkdir(parents=True, exist_ok=True)

    def iter_ready(self, *, now: datetime | None = None) -> list[SpoolEntry]:
        self.ensure()
        current = (now or datetime.now(UTC)).timestamp()
        entries: list[SpoolEntry] = []

        candidates = self._audio_candidates()
        if self._retry_file_count() >= self.max_backlog_files:
            candidates = [candidate for candidate in candidates if candidate.parent == self.retry_dir]

        for candidate in sorted(candidates, key=lambda path: path.stat().st_mtime):
            if current - candidate.stat().st_mtime < self.settle_seconds:
                continue
            entries.append(self._load_entry(candidate))

        return entries

    def backlog_file_count(self) -> int:
        self.ensure()
        return len(self._audio_candidates())

    def is_backlog_at_capacity(self) -> bool:
        return self.backlog_file_count() >= self.max_backlog_files

    def mark_failed(self, entry: SpoolEntry, error_message: str) -> SpoolEntry:
        self.ensure()

        source_path = entry.path
        target_path = source_path
        if source_path.parent != self.retry_dir:
            if self._retry_file_count() >= self.max_backlog_files:
                raise SpoolBacklogFullError(
                    f"retry backlog is full at {self.retry_dir} ({self.max_backlog_files} files)"
                )
            target_path = self.retry_dir / source_path.name
            source_path.replace(target_path)

        failed_entry = SpoolEntry(
            path=target_path,
            attempts=entry.attempts + 1,
            last_error=error_message,
        )
        self._metadata_path(target_path).write_text(
            json.dumps(
                {
                    "attempts": failed_entry.attempts,
                    "last_error": error_message,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        return failed_entry

    def mark_uploaded(self, entry: SpoolEntry) -> None:
        if entry.path.exists():
            entry.path.unlink()

        metadata_path = self._metadata_path(entry.path)
        if metadata_path.exists():
            metadata_path.unlink()

    def _audio_candidates(self) -> list[Path]:
        return [*self.root.glob("*.wav"), *self.retry_dir.glob("*.wav")]

    def _retry_file_count(self) -> int:
        return len(list(self.retry_dir.glob("*.wav")))

    def _load_entry(self, path: Path) -> SpoolEntry:
        metadata_path = self._metadata_path(path)
        if not metadata_path.exists():
            return SpoolEntry(path=path)

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return SpoolEntry(
            path=path,
            attempts=int(metadata.get("attempts", 0)),
            last_error=metadata.get("last_error"),
        )

    def _metadata_path(self, path: Path) -> Path:
        return path.with_suffix(f"{path.suffix}.json")
