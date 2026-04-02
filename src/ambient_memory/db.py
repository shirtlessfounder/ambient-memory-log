from datetime import UTC, datetime
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.config import Settings
from ambient_memory.models import AgentHeartbeat, AudioChunk, Source


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def build_engine(settings: Settings) -> Engine:
    connect_args: dict[str, str] = {}
    if settings.database_ssl_root_cert:
        connect_args["sslrootcert"] = settings.database_ssl_root_cert

    return create_engine(normalize_database_url(settings.database_url), connect_args=connect_args, future=True)


def build_session_factory(settings: Settings) -> sessionmaker[Session]:
    return sessionmaker(bind=build_engine(settings), expire_on_commit=False)


@contextmanager
def session_scope(settings: Settings) -> Iterator[Session]:
    factory = build_session_factory(settings)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def register_uploaded_chunk(
    session: Session,
    *,
    source_id: str,
    source_type: str | None = None,
    device_owner: str | None = None,
    s3_bucket: str,
    s3_key: str,
    started_at: datetime,
    ended_at: datetime,
    checksum: str | None = None,
) -> AudioChunk:
    if source_type is not None:
        upsert_source(
            session,
            source_id=source_id,
            source_type=source_type,
            device_owner=device_owner,
        )

    row = session.scalar(
        select(AudioChunk).where(
            AudioChunk.s3_bucket == s3_bucket,
            AudioChunk.s3_key == s3_key,
        )
    )

    if row is None:
        row = AudioChunk(
            source_id=source_id,
            s3_bucket=s3_bucket,
            s3_key=s3_key,
            started_at=started_at,
            ended_at=ended_at,
        )
        session.add(row)

    row.source_id = source_id
    row.s3_bucket = s3_bucket
    row.s3_key = s3_key
    row.checksum = checksum
    row.status = "uploaded"
    row.started_at = started_at
    row.ended_at = ended_at
    row.uploaded_at = datetime.now(UTC)
    row.error_message = None

    session.flush()
    return row


def upsert_source(
    session: Session,
    *,
    source_id: str,
    source_type: str,
    device_owner: str | None = None,
) -> Source:
    row = session.get(Source, source_id)
    if row is None:
        row = Source(
            id=source_id,
            source_type=source_type,
            device_owner=device_owner,
        )
        session.add(row)

    row.source_type = source_type
    if device_owner is not None:
        row.device_owner = device_owner
    row.is_active = True

    session.flush()
    return row


def record_agent_heartbeat(
    session: Session,
    *,
    source_id: str,
    seen_at: datetime | None = None,
    uploaded_at: datetime | None = None,
    source_type: str | None = None,
    device_owner: str | None = None,
) -> AgentHeartbeat:
    if source_type is not None:
        upsert_source(
            session,
            source_id=source_id,
            source_type=source_type,
            device_owner=device_owner,
        )

    heartbeat = session.get(AgentHeartbeat, source_id)
    if heartbeat is None:
        heartbeat = AgentHeartbeat(source_id=source_id)
        session.add(heartbeat)

    heartbeat.last_seen_at = seen_at or datetime.now(UTC)
    if uploaded_at is not None:
        heartbeat.last_upload_at = uploaded_at

    session.flush()
    return heartbeat
