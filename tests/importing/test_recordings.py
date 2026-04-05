from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ambient_memory.capture.uploader import UploadBatchResult
from ambient_memory.importing.recordings import (
    build_import_command,
    derive_source_id,
    run_recording_import,
    stamp_segment_files,
)


def test_build_import_command_splits_audio_into_wav_segments(tmp_path: Path) -> None:
    command = build_import_command(
        recording_path=tmp_path / "meeting.m4a",
        spool_dir=tmp_path / "imports",
        ffmpeg_binary="ffmpeg",
    )

    assert command == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-n",
        "-i",
        str(tmp_path / "meeting.m4a"),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "segment",
        "-segment_format",
        "wav",
        "-segment_time",
        "30",
        "-reset_timestamps",
        "1",
        str(tmp_path / "imports" / "segment-%06d.wav"),
    ]


def test_derive_source_id_slugifies_recording_name() -> None:
    source_id = derive_source_id(Path("/tmp/Friday Sync!!!.m4a"))

    assert source_id == "friday-sync"


def test_stamp_segment_files_uses_local_start_time_and_session_token(tmp_path: Path) -> None:
    spool_dir = tmp_path / "imports"
    spool_dir.mkdir(parents=True)
    (spool_dir / "segment-000000.wav").write_bytes(b"chunk-1")
    (spool_dir / "segment-000001.wav").write_bytes(b"chunk-2")

    stamped = stamp_segment_files(
        spool_dir=spool_dir,
        start_at=datetime(2026, 4, 3, 9, 0, tzinfo=ZoneInfo("America/New_York")),
        session_token="import123",
    )

    assert [path.name for path in stamped] == [
        "chunk-import123-20260403T090000-0400.wav",
        "chunk-import123-20260403T090030-0400.wav",
    ]


def test_run_recording_import_derives_source_id_and_uploads_chunks(tmp_path: Path) -> None:
    recording_path = tmp_path / "Friday Sync.m4a"
    recording_path.write_bytes(b"source-audio")
    calls: dict[str, object] = {}

    class Settings:
        aws_region = "us-east-1"
        s3_bucket = "ambient-memory-audio"
        database_url = "postgresql://db.example/app"
        database_ssl_root_cert = "/tmp/rds.pem"
        import_spool_dir = str(tmp_path / "imports-root")

    def fake_run_command(command: list[str]) -> None:
        calls["command"] = command
        output_dir = Path(command[-1]).parent
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "segment-000000.wav").write_bytes(b"chunk-1")
        (output_dir / "segment-000001.wav").write_bytes(b"chunk-2")

    def fake_build_s3_client(region_name: str) -> str:
        calls["region_name"] = region_name
        return "s3-client"

    def fake_build_session_factory(settings: object) -> str:
        calls["database_url"] = settings.database_url
        calls["database_ssl_root_cert"] = settings.database_ssl_root_cert
        return "session-factory"

    class FakeUploader:
        def __init__(
            self,
            *,
            spool,
            s3_client,
            session_factory,
            bucket,
            source_id,
            source_type,
            device_owner=None,
            segment_seconds=30,
            local_timezone=None,
        ) -> None:
            calls["uploader"] = {
                "spool_root": spool.root,
                "s3_client": s3_client,
                "session_factory": session_factory,
                "bucket": bucket,
                "source_id": source_id,
                "source_type": source_type,
                "device_owner": device_owner,
                "segment_seconds": segment_seconds,
                "local_timezone": local_timezone,
            }

        def upload_ready(self, *, now=None) -> UploadBatchResult:
            calls["upload_now"] = now
            return UploadBatchResult(attempted=2, uploaded=2, failed=0)

    result = run_recording_import(
        recording_path=recording_path,
        start="2026-04-03 09:00",
        source_id=None,
        ffmpeg_binary="ffmpeg",
        settings=Settings(),
        run_command=fake_run_command,
        build_s3_client=fake_build_s3_client,
        build_session_factory=fake_build_session_factory,
        uploader_factory=FakeUploader,
        session_token_factory=lambda: "session123",
        local_timezone=ZoneInfo("America/New_York"),
        now=datetime(2026, 4, 3, 15, 0, tzinfo=UTC),
    )

    assert result.source_id == "friday-sync"
    assert result.chunk_count == 2
    assert result.uploaded == 2
    assert result.failed == 0
    assert calls["region_name"] == "us-east-1"
    assert calls["database_url"] == "postgresql://db.example/app"
    assert calls["database_ssl_root_cert"] == "/tmp/rds.pem"
    assert calls["uploader"] == {
        "spool_root": tmp_path / "imports-root" / "friday-sync-session123",
        "s3_client": "s3-client",
        "session_factory": "session-factory",
        "bucket": "ambient-memory-audio",
        "source_id": "friday-sync",
        "source_type": "import",
        "device_owner": None,
        "segment_seconds": 30,
        "local_timezone": ZoneInfo("America/New_York"),
    }
    assert calls["upload_now"] == datetime(2026, 4, 3, 15, 0, tzinfo=UTC)
    assert Path(calls["command"][-1]).parent == tmp_path / "imports-root" / "friday-sync-session123"
    assert sorted(path.name for path in (tmp_path / "imports-root" / "friday-sync-session123").glob("*.wav")) == [
        "chunk-session123-20260403T090000-0400.wav",
        "chunk-session123-20260403T090030-0400.wav",
    ]
