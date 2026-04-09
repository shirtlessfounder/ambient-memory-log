import pytest

from ambient_memory.pipeline.worker import load_worker_runtime_config


def _clear_worker_env(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_SSL_ROOT_CERT", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("PYANNOTE_API_KEY", raising=False)
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)
    monkeypatch.delenv("ROOM_SPEAKER_ROSTER_PATH", raising=False)
    monkeypatch.delenv("ROOM_ASSEMBLY_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS", raising=False)


def test_worker_dry_run_config_loads_database_url_from_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://db.example/app\n", encoding="utf-8")
    _clear_worker_env(monkeypatch)

    config = load_worker_runtime_config(dry_run=True)

    assert config.database_url == "postgresql://db.example/app"
    assert config.database_ssl_root_cert is None
    assert config.aws_region is None
    assert config.deepgram_api_key is None
    assert config.pyannote_api_key is None
    assert config.assemblyai_api_key is None
    assert config.room_speaker_roster_path is None
    assert config.room_assembly_window_seconds == 600
    assert config.room_assembly_idle_flush_seconds == 120


def test_worker_dry_run_config_loads_database_url_from_explicit_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text("DATABASE_URL=postgresql://db.example/worker\n", encoding="utf-8")
    _clear_worker_env(monkeypatch)

    config = load_worker_runtime_config(dry_run=True, env_file=".env.worker")

    assert config.database_url == "postgresql://db.example/worker"
    assert config.assemblyai_api_key is None
    assert config.room_speaker_roster_path is None
    assert config.room_assembly_window_seconds == 600
    assert config.room_assembly_idle_flush_seconds == 120


def test_worker_runtime_config_requires_assemblyai_api_key_for_non_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/worker",
                "AWS_REGION=us-east-1",
                "DEEPGRAM_API_KEY=deepgram-secret",
                "PYANNOTE_API_KEY=pyannote-secret",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_worker_env(monkeypatch)

    with pytest.raises(RuntimeError, match="ASSEMBLYAI_API_KEY"):
        load_worker_runtime_config(dry_run=False, env_file=".env.worker")


def test_worker_runtime_config_loads_room_settings_for_non_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/worker",
                "AWS_REGION=us-east-1",
                "DEEPGRAM_API_KEY=deepgram-secret",
                "PYANNOTE_API_KEY=pyannote-secret",
                "ASSEMBLYAI_API_KEY=assembly-secret",
                "ROOM_SPEAKER_ROSTER_PATH=./config/room-speakers.json",
                "ROOM_ASSEMBLY_WINDOW_SECONDS=900",
                "ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS=180",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_worker_env(monkeypatch)

    config = load_worker_runtime_config(dry_run=False, env_file=".env.worker")

    assert config.room_speaker_roster_path == "./config/room-speakers.json"
    assert config.room_assembly_window_seconds == 900
    assert config.room_assembly_idle_flush_seconds == 180


def test_worker_runtime_config_requires_room_speaker_roster_path_for_non_dry_run(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/worker",
                "AWS_REGION=us-east-1",
                "DEEPGRAM_API_KEY=deepgram-secret",
                "PYANNOTE_API_KEY=pyannote-secret",
                "ASSEMBLYAI_API_KEY=assembly-secret",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_worker_env(monkeypatch)

    with pytest.raises(RuntimeError, match="ROOM_SPEAKER_ROSTER_PATH"):
        load_worker_runtime_config(dry_run=False, env_file=".env.worker")


def test_worker_runtime_config_rejects_non_positive_room_window_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text(
        "\n".join(
            (
                "DATABASE_URL=postgresql://db.example/worker",
                "AWS_REGION=us-east-1",
                "DEEPGRAM_API_KEY=deepgram-secret",
                "PYANNOTE_API_KEY=pyannote-secret",
                "ASSEMBLYAI_API_KEY=assembly-secret",
                "ROOM_SPEAKER_ROSTER_PATH=./config/room-speakers.json",
                "ROOM_ASSEMBLY_WINDOW_SECONDS=0",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_worker_env(monkeypatch)

    with pytest.raises(Exception, match="ROOM_ASSEMBLY_WINDOW_SECONDS|room_assembly_window_seconds"):
        load_worker_runtime_config(dry_run=False, env_file=".env.worker")
