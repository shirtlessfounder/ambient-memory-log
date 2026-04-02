from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.config import Settings


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
