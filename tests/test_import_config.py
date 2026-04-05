from ambient_memory.config import ImportSettings


def test_import_settings_only_require_database_bucket_and_region(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/app")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET", "ambient-memory-audio")
    monkeypatch.delenv("DATABASE_SSL_ROOT_CERT", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("PYANNOTE_API_KEY", raising=False)
    monkeypatch.delenv("SOURCE_ID", raising=False)
    monkeypatch.delenv("SOURCE_TYPE", raising=False)
    monkeypatch.delenv("SPOOL_DIR", raising=False)

    settings = ImportSettings()

    assert settings.database_url == "postgresql://db.example/app"
    assert settings.aws_region == "us-east-1"
    assert settings.s3_bucket == "ambient-memory-audio"
    assert settings.database_ssl_root_cert is None
    assert settings.import_spool_dir == "./spool/imports"
