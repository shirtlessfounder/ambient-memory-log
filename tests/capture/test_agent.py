from pathlib import Path

from ambient_memory.capture.agent import load_runtime_config


def _clear_agent_env(monkeypatch) -> None:
    for name in (
        "SOURCE_ID",
        "SOURCE_TYPE",
        "DEVICE_OWNER",
        "SPOOL_DIR",
        "ACTIVE_START_LOCAL",
        "ACTIVE_END_LOCAL",
        "AWS_REGION",
        "S3_BUCKET",
        "DATABASE_URL",
        "DATABASE_SSL_ROOT_CERT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_load_runtime_config_dry_run_reads_capture_settings_from_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "SOURCE_ID=room-1",
                "SOURCE_TYPE=room",
                "DEVICE_OWNER=conference-room",
                "SPOOL_DIR=./spool/room-1",
                "ACTIVE_START_LOCAL=08:30",
                "ACTIVE_END_LOCAL=22:15",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_agent_env(monkeypatch)

    config = load_runtime_config(dry_run=True)

    assert config.source_id == "room-1"
    assert config.source_type == "room"
    assert config.device_owner == "conference-room"
    assert config.spool_dir == Path("./spool/room-1")
    assert config.active_start_local == "08:30"
    assert config.active_end_local == "22:15"


def test_load_runtime_config_non_dry_run_reads_required_settings_from_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "SOURCE_ID=desk-a",
                "SOURCE_TYPE=macbook",
                "DEVICE_OWNER=dylan",
                "SPOOL_DIR=./spool/desk-a",
                "ACTIVE_START_LOCAL=09:00",
                "ACTIVE_END_LOCAL=00:00",
                "AWS_REGION=us-east-1",
                "S3_BUCKET=ambient-memory",
                "DATABASE_URL=postgresql://db.example/app",
                "DATABASE_SSL_ROOT_CERT=/tmp/rds.pem",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_agent_env(monkeypatch)

    config = load_runtime_config(dry_run=False)

    assert config.source_id == "desk-a"
    assert config.source_type == "macbook"
    assert config.device_owner == "dylan"
    assert config.spool_dir == Path("./spool/desk-a")
    assert config.aws_region == "us-east-1"
    assert config.s3_bucket == "ambient-memory"
    assert config.database_url == "postgresql://db.example/app"
    assert config.database_ssl_root_cert == "/tmp/rds.pem"
