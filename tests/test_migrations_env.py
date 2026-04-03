from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeTransaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeAlembicContext:
    def __init__(self, *, offline_mode: bool) -> None:
        self.config = SimpleNamespace(config_file_name=None)
        self.configure_calls: list[dict[str, object]] = []
        self.ran_migrations = False
        self.offline_mode = offline_mode

    def is_offline_mode(self) -> bool:
        return self.offline_mode

    def configure(self, **kwargs: object) -> None:
        self.configure_calls.append(kwargs)

    def begin_transaction(self) -> FakeTransaction:
        return FakeTransaction()

    def run_migrations(self) -> None:
        self.ran_migrations = True


class FakeConnection:
    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeEngine:
    def __init__(self) -> None:
        self.execution_options_calls: list[dict[str, object]] = []
        self.connection = FakeConnection()

    def execution_options(self, **kwargs: object) -> "FakeEngine":
        self.execution_options_calls.append(kwargs)
        return self

    def connect(self) -> FakeConnection:
        return self.connection


def test_migrations_env_starts_with_database_settings_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://db.example/app\n", encoding="utf-8")
    for name in (
        "DATABASE_URL",
        "DATABASE_SSL_ROOT_CERT",
        "AWS_REGION",
        "S3_BUCKET",
        "DEEPGRAM_API_KEY",
        "PYANNOTE_API_KEY",
        "SOURCE_ID",
        "SOURCE_TYPE",
        "SPOOL_DIR",
        "ACTIVE_START_LOCAL",
        "ACTIVE_END_LOCAL",
    ):
        monkeypatch.delenv(name, raising=False)

    fake_context = FakeAlembicContext(offline_mode=True)
    fake_alembic = ModuleType("alembic")
    fake_alembic.context = fake_context
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)

    runpy.run_path(str(REPO_ROOT / "migrations" / "env.py"))

    assert fake_context.configure_calls == [
        {
            "url": "postgresql://db.example/app",
            "target_metadata": fake_context.configure_calls[0]["target_metadata"],
            "literal_binds": True,
            "dialect_opts": {"paramstyle": "named"},
        }
    ]
    assert fake_context.ran_migrations is True


def test_migrations_env_online_uses_database_settings_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/app",
                "DATABASE_SSL_ROOT_CERT=/tmp/rds.pem",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    for name in (
        "DATABASE_URL",
        "DATABASE_SSL_ROOT_CERT",
        "AWS_REGION",
        "S3_BUCKET",
        "DEEPGRAM_API_KEY",
        "PYANNOTE_API_KEY",
        "SOURCE_ID",
        "SOURCE_TYPE",
        "SPOOL_DIR",
        "ACTIVE_START_LOCAL",
        "ACTIVE_END_LOCAL",
    ):
        monkeypatch.delenv(name, raising=False)

    fake_context = FakeAlembicContext(offline_mode=False)
    fake_alembic = ModuleType("alembic")
    fake_alembic.context = fake_context
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)

    fake_engine = FakeEngine()

    from ambient_memory import db as db_module

    build_engine_calls: list[object] = []

    def fake_build_engine(settings: object) -> FakeEngine:
        build_engine_calls.append(settings)
        return fake_engine

    monkeypatch.setattr(db_module, "build_engine", fake_build_engine)

    runpy.run_path(str(REPO_ROOT / "migrations" / "env.py"))

    assert len(build_engine_calls) == 1
    settings = build_engine_calls[0]
    assert settings.database_url == "postgresql://db.example/app"
    assert settings.database_ssl_root_cert == "/tmp/rds.pem"
    assert fake_engine.execution_options_calls == [{"isolation_level": "AUTOCOMMIT"}]
    assert fake_context.configure_calls == [
        {
            "connection": fake_engine.connection,
            "target_metadata": fake_context.configure_calls[0]["target_metadata"],
        }
    ]
    assert fake_context.ran_migrations is True
