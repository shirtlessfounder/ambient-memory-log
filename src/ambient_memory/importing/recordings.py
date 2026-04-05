from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from ambient_memory.capture.agent import build_s3_client as build_capture_s3_client
from ambient_memory.capture.ffmpeg import DEFAULT_CHANNELS, DEFAULT_SAMPLE_RATE, DEFAULT_SEGMENT_SECONDS
from ambient_memory.capture.spool import LocalSpool
from ambient_memory.capture.uploader import ChunkUploader
from ambient_memory.config import DatabaseSettings, ImportSettings
from ambient_memory.db import build_session_factory as build_db_session_factory


SEGMENT_OUTPUT_TEMPLATE = "segment-%06d.wav"
SEGMENT_FILENAME_PATTERN = re.compile(r"^segment-(?P<index>\d{6})\.wav$")
START_TIME_FORMAT = "%Y-%m-%d %H:%M"


@dataclass(frozen=True, slots=True)
class ImportResult:
    source_id: str
    spool_dir: Path
    chunk_count: int
    uploaded: int
    failed: int


def build_import_command(
    *,
    recording_path: Path | str,
    spool_dir: Path | str,
    ffmpeg_binary: str = "ffmpeg",
    segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
) -> list[str]:
    return [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-n",
        "-i",
        str(recording_path),
        "-vn",
        "-ac",
        str(DEFAULT_CHANNELS),
        "-ar",
        str(DEFAULT_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-f",
        "segment",
        "-segment_format",
        "wav",
        "-segment_time",
        str(segment_seconds),
        "-reset_timestamps",
        "1",
        str(Path(spool_dir) / SEGMENT_OUTPUT_TEMPLATE),
    ]


def derive_source_id(recording_path: Path | str) -> str:
    stem = Path(recording_path).stem.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return slug or "import"


def stamp_segment_files(
    *,
    spool_dir: Path | str,
    start_at: datetime,
    session_token: str,
    segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
) -> list[Path]:
    stamped: list[Path] = []

    for path in sorted(Path(spool_dir).glob("segment-*.wav")):
        match = SEGMENT_FILENAME_PATTERN.match(path.name)
        if match is None:
            continue

        chunk_started_at = start_at + timedelta(seconds=int(match.group("index")) * segment_seconds)
        target = path.with_name(f"chunk-{session_token}-{chunk_started_at.strftime('%Y%m%dT%H%M%S%z')}.wav")
        path.rename(target)
        stamped.append(target)

    return stamped


def run_recording_import(
    *,
    recording_path: Path,
    start: str,
    source_id: str | None,
    ffmpeg_binary: str = "ffmpeg",
    settings: ImportSettings | None = None,
    run_command: Callable[[list[str]], None] | None = None,
    build_s3_client: Callable[[str], object] = build_capture_s3_client,
    build_session_factory: Callable[[DatabaseSettings], object] = build_db_session_factory,
    uploader_factory: Callable[..., object] = ChunkUploader,
    session_token_factory: Callable[[], str] | None = None,
    local_timezone: tzinfo | None = None,
    now: datetime | None = None,
) -> ImportResult:
    runtime_settings = settings or ImportSettings()
    resolved_source_id = source_id or derive_source_id(recording_path)
    timezone = local_timezone or datetime.now().astimezone().tzinfo or UTC
    started_at = datetime.strptime(start, START_TIME_FORMAT).replace(tzinfo=timezone)
    session_token = (session_token_factory or _default_session_token)()
    spool_dir = Path(runtime_settings.import_spool_dir) / f"{resolved_source_id}-{session_token}"
    spool = LocalSpool(spool_dir, settle_seconds=0)
    spool.ensure()

    command = build_import_command(
        recording_path=recording_path,
        spool_dir=spool_dir,
        ffmpeg_binary=ffmpeg_binary,
    )
    (run_command or _run_command)(command)

    stamped = stamp_segment_files(
        spool_dir=spool_dir,
        start_at=started_at,
        session_token=session_token,
    )
    if not stamped:
        raise RuntimeError(f"ffmpeg did not create any chunks for {recording_path}")

    uploader = uploader_factory(
        spool=spool,
        s3_client=build_s3_client(runtime_settings.aws_region),
        session_factory=build_session_factory(
            DatabaseSettings(
                database_url=runtime_settings.database_url,
                database_ssl_root_cert=runtime_settings.database_ssl_root_cert,
            )
        ),
        bucket=runtime_settings.s3_bucket,
        source_id=resolved_source_id,
        source_type="import",
        local_timezone=timezone,
    )
    upload_result = uploader.upload_ready(now=now or datetime.now(UTC))

    return ImportResult(
        source_id=resolved_source_id,
        spool_dir=spool_dir,
        chunk_count=len(stamped),
        uploaded=upload_result.uploaded,
        failed=upload_result.failed,
    )


def _default_session_token() -> str:
    return uuid4().hex[:8]


def _run_command(command: list[str]) -> None:
    subprocess.run(command, check=True)
