from ambient_memory.pipeline.worker import load_worker_runtime_config


def test_worker_dry_run_config_loads_database_url_from_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://db.example/app\n", encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_SSL_ROOT_CERT", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("PYANNOTE_API_KEY", raising=False)

    config = load_worker_runtime_config(dry_run=True)

    assert config.database_url == "postgresql://db.example/app"
    assert config.database_ssl_root_cert is None
    assert config.aws_region is None
    assert config.deepgram_api_key is None
    assert config.pyannote_api_key is None
