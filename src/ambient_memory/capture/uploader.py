from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
import re

from sqlalchemy.orm import Session

from ambient_memory.capture.spool import LocalSpool, SpoolEntry
from ambient_memory.db import record_agent_heartbeat, register_uploaded_chunk
from ambient_memory.integrations.s3_store import upload_chunk


CHUNK_FILENAME_PATTERN = re.compile(r"(\d{8}T\d{6})")


@dataclass(frozen=True, slots=True)
class UploadBatchResult:
    attempted: int = 0
    uploaded: int = 0
    failed: int = 0


class ChunkUploader:
    def __init__(
        self,
        *,
        spool: LocalSpool,
        s3_client: object,
        session_factory: Callable[[], Session],
        bucket: str,
        source_id: str,
        source_type: str,
        device_owner: str | None = None,
        segment_seconds: int = 30,
    ) -> None:
        self.spool = spool
        self.s3_client = s3_client
        self.session_factory = session_factory
        self.bucket = bucket
        self.source_id = source_id
        self.source_type = source_type
        self.device_owner = device_owner
        self.segment_seconds = segment_seconds
        self.local_tz = datetime.now().astimezone().tzinfo or UTC

    def upload_ready(self) -> UploadBatchResult:
        attempted = 0
        uploaded = 0
        failed = 0

        for entry in self.spool.iter_ready():
            attempted += 1
            if self._upload_entry(entry):
                uploaded += 1
            else:
                failed += 1

        return UploadBatchResult(attempted=attempted, uploaded=uploaded, failed=failed)

    def _upload_entry(self, entry: SpoolEntry) -> bool:
        now = datetime.now(UTC)
        started_at = self._started_at_for(entry.path)
        ended_at = started_at + timedelta(seconds=self.segment_seconds)
        checksum = self._checksum_for(entry.path)

        try:
            with entry.path.open("rb") as handle:
                s3_key = upload_chunk(
                    client=self.s3_client,
                    bucket=self.bucket,
                    source_id=self.source_id,
                    started_at=started_at,
                    body=handle.read(),
                    extension=entry.path.suffix.lstrip(".") or "wav",
                    content_type=self._content_type_for(entry.path),
                    metadata=self._metadata(),
                )

            session = self.session_factory()
            try:
                register_uploaded_chunk(
                    session,
                    source_id=self.source_id,
                    source_type=self.source_type,
                    device_owner=self.device_owner,
                    s3_bucket=self.bucket,
                    s3_key=s3_key,
                    started_at=started_at,
                    ended_at=ended_at,
                    checksum=checksum,
                )
                record_agent_heartbeat(
                    session,
                    source_id=self.source_id,
                    source_type=self.source_type,
                    device_owner=self.device_owner,
                    seen_at=now,
                    uploaded_at=now,
                )
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

            self.spool.mark_uploaded(entry)
            return True
        except Exception as exc:
            self.spool.mark_failed(entry, str(exc))
            return False

    def _metadata(self) -> dict[str, str]:
        metadata = {
            "source_id": self.source_id,
            "source_type": self.source_type,
        }
        if self.device_owner:
            metadata["device_owner"] = self.device_owner
        return metadata

    def _started_at_for(self, path: Path) -> datetime:
        match = CHUNK_FILENAME_PATTERN.search(path.name)
        if match is None:
            raise ValueError(f"chunk filename does not include timestamp: {path.name}")

        naive_started_at = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
        return naive_started_at.replace(tzinfo=UTC)

    def _checksum_for(self, path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 64), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    def _content_type_for(self, path: Path) -> str:
        if path.suffix.lower() == ".wav":
            return "audio/wav"
        return "application/octet-stream"
