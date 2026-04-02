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
    def __init__(self) -> None:
        self.config = SimpleNamespace(config_file_name=None)
        self.configure_calls: list[dict[str, object]] = []
        self.ran_migrations = False

    def is_offline_mode(self) -> bool:
        return True

    def configure(self, **kwargs: object) -> None:
        self.configure_calls.append(kwargs)

    def begin_transaction(self) -> FakeTransaction:
        return FakeTransaction()

    def run_migrations(self) -> None:
        self.ran_migrations = True


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

    fake_context = FakeAlembicContext()
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
