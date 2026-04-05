from ambient_memory.config import EnrollmentSettings


def test_enrollment_settings_only_require_database_and_pyannote(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/app")
    monkeypatch.setenv("PYANNOTE_API_KEY", "pyannote-secret")
    monkeypatch.delenv("DATABASE_SSL_ROOT_CERT", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("SOURCE_ID", raising=False)
    monkeypatch.delenv("SOURCE_TYPE", raising=False)
    monkeypatch.delenv("SPOOL_DIR", raising=False)
    monkeypatch.delenv("ACTIVE_START_LOCAL", raising=False)
    monkeypatch.delenv("ACTIVE_END_LOCAL", raising=False)

    settings = EnrollmentSettings()

    assert settings.database_url == "postgresql://db.example/app"
    assert settings.pyannote_api_key == "pyannote-secret"
    assert settings.database_ssl_root_cert is None


def test_enrollment_settings_accept_optional_database_ssl_root_cert(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example/app")
    monkeypatch.setenv("PYANNOTE_API_KEY", "pyannote-secret")
    monkeypatch.setenv("DATABASE_SSL_ROOT_CERT", "/tmp/rds-ca.pem")

    settings = EnrollmentSettings()

    assert settings.database_ssl_root_cert == "/tmp/rds-ca.pem"
