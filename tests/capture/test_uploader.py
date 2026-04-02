from datetime import UTC, datetime, timedelta
import importlib
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ambient_memory.models import AudioChunk, Source


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


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Source.__table__.create(bind=engine)
    AudioChunk.__table__.create(bind=engine)

    session = Session(engine)
    session.add(Source(id="desk-a", source_type="macbook", device_owner="Dylan"))
    session.commit()

    try:
        yield session
    finally:
        session.close()


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
        status="failed",
        started_at=started_at,
        ended_at=ended_at,
        error_message="network error",
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
