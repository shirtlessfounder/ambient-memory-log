from datetime import UTC, datetime, timedelta
import importlib
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.models import AgentHeartbeat, AudioChunk, Source


def load_s3_store() -> Any:
    try:
        module = importlib.import_module("ambient_memory.integrations.s3_store")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing s3 store module: {exc}")

    for name in ("build_chunk_key", "upload_chunk", "presign_chunk_url"):
        if not hasattr(module, name):
            pytest.fail(f"missing s3 helper: {name}")

    return module


def load_register_uploaded_chunk() -> Any:
    module = importlib.import_module("ambient_memory.db")

    register_uploaded_chunk = getattr(module, "register_uploaded_chunk", None)
    if register_uploaded_chunk is None:
        pytest.fail("missing db helper: register_uploaded_chunk")

    return register_uploaded_chunk


def load_uploader_module() -> Any:
    try:
        module = importlib.import_module("ambient_memory.capture.uploader")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing uploader module: {exc}")

    if not hasattr(module, "ChunkUploader"):
        pytest.fail("missing uploader symbol: ChunkUploader")

    return module


def load_spool_module() -> Any:
    try:
        module = importlib.import_module("ambient_memory.capture.spool")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing spool module: {exc}")

    if not hasattr(module, "LocalSpool"):
        pytest.fail("missing spool symbol: LocalSpool")

    return module


class RecordingS3Client:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.presign_calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, str]:
        self.put_calls.append(kwargs)
        return {"ETag": '"etag-123"'}

    def generate_presigned_url(self, client_method: str, *, Params: dict[str, str], ExpiresIn: int) -> str:
        self.presign_calls.append(
            {
                "client_method": client_method,
                "params": Params,
                "expires_in": ExpiresIn,
            }
        )
        return f"https://example.test/{Params['Bucket']}/{Params['Key']}?expires={ExpiresIn}"


class FailingS3Client:
    def put_object(self, **_: Any) -> dict[str, str]:
        raise RuntimeError("network down")


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Source.__table__.create(bind=engine)
    AudioChunk.__table__.create(bind=engine)
    AgentHeartbeat.__table__.create(bind=engine)

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.add(Source(id="desk-a", source_type="macbook", device_owner="Dylan"))
    session.commit()
    session.close()

    return factory


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Session:
    session = session_factory()

    try:
        yield session
    finally:
        session.close()


def write_chunk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio-bytes")
    return path


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def test_chunk_key_includes_source_and_timestamp() -> None:
    s3_store = load_s3_store()

    key = s3_store.build_chunk_key("desk-a", datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC))

    assert key == "raw-audio/desk-a/2026/04/02/20260402T090000.000000Z.wav"


def test_chunk_key_avoids_same_second_collisions_for_same_source() -> None:
    s3_store = load_s3_store()

    first_key = s3_store.build_chunk_key(
        "desk-a",
        datetime(2026, 4, 2, 9, 0, 0, 123456, tzinfo=UTC),
    )
    second_key = s3_store.build_chunk_key(
        "desk-a",
        datetime(2026, 4, 2, 9, 0, 0, 654321, tzinfo=UTC),
    )

    assert first_key.startswith("raw-audio/desk-a/2026/04/02/")
    assert second_key.startswith("raw-audio/desk-a/2026/04/02/")
    assert first_key != second_key


def test_chunk_key_preserves_session_token_for_same_second_chunks() -> None:
    s3_store = load_s3_store()
    started_at = datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC)

    first_key = s3_store.build_chunk_key(
        "desk-a",
        started_at,
        uniqueness_token="session-a",
    )
    second_key = s3_store.build_chunk_key(
        "desk-a",
        started_at,
        uniqueness_token="session-b",
    )

    assert first_key == "raw-audio/desk-a/2026/04/02/20260402T130000.000000Z-session-a.wav"
    assert second_key == "raw-audio/desk-a/2026/04/02/20260402T130000.000000Z-session-b.wav"
    assert first_key != second_key


def test_upload_chunk_uses_deterministic_key() -> None:
    s3_store = load_s3_store()
    client = RecordingS3Client()
    started_at = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)

    key = s3_store.upload_chunk(
        client=client,
        bucket="ambient-memory",
        source_id="desk-a",
        started_at=started_at,
        body=b"audio-bytes",
    )

    assert key == "raw-audio/desk-a/2026/04/02/20260402T090000.000000Z.wav"
    assert client.put_calls == [
        {
            "Bucket": "ambient-memory",
            "Key": key,
            "Body": b"audio-bytes",
            "ContentType": "audio/wav",
            "Metadata": {},
        }
    ]


def test_presign_chunk_url_uses_get_object() -> None:
    s3_store = load_s3_store()
    client = RecordingS3Client()

    url = s3_store.presign_chunk_url(
        client=client,
        bucket="ambient-memory",
        key="raw-audio/desk-a/2026/04/02/20260402T090000.000000Z.wav",
        expires_in=900,
    )

    assert url.endswith("expires=900")
    assert client.presign_calls == [
        {
            "client_method": "get_object",
            "params": {
                "Bucket": "ambient-memory",
                "Key": "raw-audio/desk-a/2026/04/02/20260402T090000.000000Z.wav",
            },
            "expires_in": 900,
        }
    ]


def test_register_uploaded_chunk_creates_uploaded_row(session: Session) -> None:
    register_uploaded_chunk = load_register_uploaded_chunk()
    started_at = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)
    ended_at = started_at + timedelta(seconds=30)

    row = register_uploaded_chunk(
        session,
        source_id="desk-a",
        s3_bucket="ambient-memory",
        s3_key="raw-audio/desk-a/2026/04/02/20260402T090000.000000Z.wav",
        started_at=started_at,
        ended_at=ended_at,
        checksum="sha256:abc123",
    )

    stored = session.scalar(select(AudioChunk).where(AudioChunk.id == row.id))

    assert stored is not None
    assert stored.status == "uploaded"
    assert stored.source_id == "desk-a"
    assert stored.checksum == "sha256:abc123"
    assert stored.error_message is None


def test_register_uploaded_chunk_updates_existing_row(session: Session) -> None:
    register_uploaded_chunk = load_register_uploaded_chunk()
    started_at = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)
    ended_at = started_at + timedelta(seconds=30)
    key = "raw-audio/desk-a/2026/04/02/20260402T090000.000000Z.wav"

    existing = AudioChunk(
        source_id="desk-a",
        s3_bucket="ambient-memory",
        s3_key=key,
        checksum="sha256:old",
        status="uploaded",
        started_at=started_at,
        ended_at=ended_at,
    )
    session.add(existing)
    session.commit()

    row = register_uploaded_chunk(
        session,
        source_id="desk-a",
        s3_bucket="ambient-memory",
        s3_key=key,
        started_at=started_at,
        ended_at=ended_at,
        checksum="sha256:new",
    )

    rows = session.scalars(select(AudioChunk)).all()

    assert row.id == existing.id
    assert len(rows) == 1
    assert row.status == "uploaded"
    assert row.checksum == "sha256:new"
    assert row.error_message is None


@pytest.mark.parametrize(("status", "error_message"), [("processed", None), ("failed", "worker error")])
def test_register_uploaded_chunk_preserves_terminal_status_for_existing_row(
    session: Session,
    status: str,
    error_message: str | None,
) -> None:
    register_uploaded_chunk = load_register_uploaded_chunk()
    started_at = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)
    ended_at = started_at + timedelta(seconds=30)
    uploaded_at = datetime(2026, 4, 2, 9, 5, 0, tzinfo=UTC)
    key = "raw-audio/desk-a/2026/04/02/20260402T090000.000000Z.wav"

    existing = AudioChunk(
        source_id="desk-a",
        s3_bucket="ambient-memory",
        s3_key=key,
        checksum="sha256:old",
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        uploaded_at=uploaded_at,
        error_message=error_message,
    )
    session.add(existing)
    session.commit()

    row = register_uploaded_chunk(
        session,
        source_id="desk-a",
        s3_bucket="ambient-memory",
        s3_key=key,
        started_at=started_at,
        ended_at=ended_at,
        checksum="sha256:new",
    )

    assert row.id == existing.id
    assert row.status == status
    assert row.checksum == "sha256:old"
    assert as_utc(row.uploaded_at) == uploaded_at
    assert row.error_message == error_message


def test_chunk_uploader_uploads_ready_chunks_and_updates_heartbeat(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    uploader_module = load_uploader_module()
    spool_module = load_spool_module()
    client = RecordingS3Client()
    spool = spool_module.LocalSpool(tmp_path / "spool", settle_seconds=0)
    write_chunk(spool.root / "chunk-session-20260402T090000-0400.wav")

    uploader = uploader_module.ChunkUploader(
        spool=spool,
        s3_client=client,
        session_factory=session_factory,
        bucket="ambient-memory",
        source_id="desk-a",
        source_type="macbook",
        device_owner="Dylan",
        local_timezone=ZoneInfo("America/New_York"),
    )

    result = uploader.upload_ready()

    session = session_factory()
    try:
        stored_chunk = session.scalar(select(AudioChunk))
        heartbeat = session.scalar(select(AgentHeartbeat))
    finally:
        session.close()

    assert result.attempted == 1
    assert result.uploaded == 1
    assert result.failed == 0
    assert stored_chunk is not None
    assert stored_chunk.status == "uploaded"
    assert as_utc(stored_chunk.started_at) == datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC)
    assert as_utc(stored_chunk.ended_at) == datetime(2026, 4, 2, 13, 0, 30, tzinfo=UTC)
    assert stored_chunk.s3_key == "raw-audio/desk-a/2026/04/02/20260402T130000.000000Z-session.wav"
    assert heartbeat is not None
    assert heartbeat.source_id == "desk-a"
    assert heartbeat.last_upload_at is not None
    assert client.put_calls[0]["Metadata"]["source_type"] == "macbook"
    assert not list(spool.root.glob("*.wav"))


def test_chunk_uploader_moves_failed_uploads_into_retry_backlog(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    uploader_module = load_uploader_module()
    spool_module = load_spool_module()
    spool = spool_module.LocalSpool(tmp_path / "spool", settle_seconds=0)
    write_chunk(spool.root / "chunk-session-20260402T090000.wav")

    uploader = uploader_module.ChunkUploader(
        spool=spool,
        s3_client=FailingS3Client(),
        session_factory=session_factory,
        bucket="ambient-memory",
        source_id="desk-a",
        source_type="macbook",
        device_owner="Dylan",
        local_timezone=ZoneInfo("America/New_York"),
    )

    result = uploader.upload_ready()

    retry_entries = spool.iter_ready()

    assert result.attempted == 1
    assert result.uploaded == 0
    assert result.failed == 1
    assert retry_entries[0].path.parent == spool.retry_dir
    assert retry_entries[0].attempts == 1


def test_chunk_uploader_keeps_same_second_cross_run_chunks_distinct(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    uploader_module = load_uploader_module()
    spool_module = load_spool_module()
    client = RecordingS3Client()
    spool = spool_module.LocalSpool(tmp_path / "spool", settle_seconds=0)
    write_chunk(spool.root / "chunk-session-a-20260402T090000.wav")
    write_chunk(spool.root / "chunk-session-b-20260402T090000.wav")

    uploader = uploader_module.ChunkUploader(
        spool=spool,
        s3_client=client,
        session_factory=session_factory,
        bucket="ambient-memory",
        source_id="desk-a",
        source_type="macbook",
        device_owner="Dylan",
        local_timezone=ZoneInfo("America/New_York"),
    )

    result = uploader.upload_ready()

    session = session_factory()
    try:
        rows = session.scalars(select(AudioChunk).order_by(AudioChunk.s3_key)).all()
    finally:
        session.close()

    assert result.attempted == 2
    assert result.uploaded == 2
    assert result.failed == 0
    assert [as_utc(row.started_at) for row in rows] == [
        datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
        datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC),
    ]
    assert [row.s3_key for row in rows] == [
        "raw-audio/desk-a/2026/04/02/20260402T130000.000000Z-session-a.wav",
        "raw-audio/desk-a/2026/04/02/20260402T130000.000000Z-session-b.wav",
    ]
    assert [call["Key"] for call in client.put_calls] == [
        "raw-audio/desk-a/2026/04/02/20260402T130000.000000Z-session-a.wav",
        "raw-audio/desk-a/2026/04/02/20260402T130000.000000Z-session-b.wav",
    ]


def test_chunk_uploader_parses_offset_timestamps_across_dst_fallback(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
) -> None:
    uploader_module = load_uploader_module()
    spool_module = load_spool_module()
    client = RecordingS3Client()
    spool = spool_module.LocalSpool(tmp_path / "spool", settle_seconds=0)
    write_chunk(spool.root / "chunk-session-20261101T013000-0400.wav")
    write_chunk(spool.root / "chunk-session-20261101T013000-0500.wav")

    uploader = uploader_module.ChunkUploader(
        spool=spool,
        s3_client=client,
        session_factory=session_factory,
        bucket="ambient-memory",
        source_id="desk-a",
        source_type="macbook",
        device_owner="Dylan",
        local_timezone=ZoneInfo("America/New_York"),
    )

    result = uploader.upload_ready()

    session = session_factory()
    try:
        rows = session.scalars(select(AudioChunk).order_by(AudioChunk.started_at)).all()
    finally:
        session.close()

    assert result.attempted == 2
    assert result.uploaded == 2
    assert result.failed == 0
    assert [as_utc(row.started_at) for row in rows] == [
        datetime(2026, 11, 1, 5, 30, 0, tzinfo=UTC),
        datetime(2026, 11, 1, 6, 30, 0, tzinfo=UTC),
    ]
    assert [row.s3_key for row in rows] == [
        "raw-audio/desk-a/2026/11/01/20261101T053000.000000Z-session.wav",
        "raw-audio/desk-a/2026/11/01/20261101T063000.000000Z-session.wav",
    ]
