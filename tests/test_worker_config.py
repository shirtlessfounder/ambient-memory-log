import pytest

from ambient_memory.pipeline.worker import load_worker_runtime_config


def test_worker_dry_run_config_loads_database_url_from_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://db.example/app\n", encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_SSL_ROOT_CERT", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("PYANNOTE_API_KEY", raising=False)
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)

    config = load_worker_runtime_config(dry_run=True)

    assert config.database_url == "postgresql://db.example/app"
    assert config.database_ssl_root_cert is None
    assert config.aws_region is None
    assert config.deepgram_api_key is None
    assert config.pyannote_api_key is None
    assert config.assemblyai_api_key is None


def test_worker_dry_run_config_loads_database_url_from_explicit_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.worker").write_text("DATABASE_URL=postgresql://db.example/worker\n", encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_SSL_ROOT_CERT", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("PYANNOTE_API_KEY", raising=False)
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)

    config = load_worker_runtime_config(dry_run=True, env_file=".env.worker")

    assert config.database_url == "postgresql://db.example/worker"
    assert config.assemblyai_api_key is None


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
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_SSL_ROOT_CERT", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("PYANNOTE_API_KEY", raising=False)
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ASSEMBLYAI_API_KEY"):
        load_worker_runtime_config(dry_run=False, env_file=".env.worker")
