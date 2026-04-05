from pathlib import Path
from typing import TypeVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


COMMON_MODEL_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    populate_by_name=True,
    extra="ignore",
)


SettingsT = TypeVar("SettingsT", bound=BaseSettings)


def load_settings(settings_type: type[SettingsT], *, env_file: str | Path | None = None) -> SettingsT:
    if env_file is None:
        return settings_type()
    return settings_type(_env_file=str(env_file))


class DatabaseSettings(BaseSettings):
    database_url: str = Field(alias="DATABASE_URL")
    database_ssl_root_cert: str | None = Field(default=None, alias="DATABASE_SSL_ROOT_CERT")

    model_config = COMMON_MODEL_CONFIG


class CaptureSettings(BaseSettings):
    source_id: str = Field(default="desk-a", alias="SOURCE_ID")
    source_type: str = Field(default="macbook", alias="SOURCE_TYPE")
    device_owner: str | None = Field(default=None, alias="DEVICE_OWNER")
    spool_dir: str = Field(default="./spool", alias="SPOOL_DIR")
    capture_device_name: str | None = Field(default=None, alias="CAPTURE_DEVICE_NAME")
    capture_max_backlog_files: int = Field(default=2048, alias="CAPTURE_MAX_BACKLOG_FILES", gt=0)
    silence_filter_enabled: bool = Field(default=True, alias="SILENCE_FILTER_ENABLED")
    silence_max_volume_db: float = Field(default=-45.0, alias="SILENCE_MAX_VOLUME_DB")
    active_start_local: str = Field(default="09:00", alias="ACTIVE_START_LOCAL")
    active_end_local: str = Field(default="00:00", alias="ACTIVE_END_LOCAL")
    aws_region: str | None = Field(default=None, alias="AWS_REGION")
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    database_ssl_root_cert: str | None = Field(default=None, alias="DATABASE_SSL_ROOT_CERT")

    model_config = COMMON_MODEL_CONFIG


class EnrollmentSettings(DatabaseSettings):
    pyannote_api_key: str = Field(alias="PYANNOTE_API_KEY")

    model_config = COMMON_MODEL_CONFIG


class ImportSettings(DatabaseSettings):
    aws_region: str = Field(alias="AWS_REGION")
    s3_bucket: str = Field(alias="S3_BUCKET")
    import_spool_dir: str = Field(default="./spool/imports", alias="IMPORT_SPOOL_DIR")

    model_config = COMMON_MODEL_CONFIG


class WorkerSettings(DatabaseSettings):
    aws_region: str | None = Field(default=None, alias="AWS_REGION")
    deepgram_api_key: str | None = Field(default=None, alias="DEEPGRAM_API_KEY")
    pyannote_api_key: str | None = Field(default=None, alias="PYANNOTE_API_KEY")

    model_config = COMMON_MODEL_CONFIG


class ApiSettings(DatabaseSettings):
    aws_region: str | None = Field(default=None, alias="AWS_REGION")
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_presign_expires_in: int = Field(default=3600, alias="API_PRESIGN_EXPIRES_IN")

    model_config = COMMON_MODEL_CONFIG


class Settings(DatabaseSettings):
    aws_region: str = Field(alias="AWS_REGION")
    s3_bucket: str = Field(alias="S3_BUCKET")
    pyannote_api_key: str = Field(alias="PYANNOTE_API_KEY")
    deepgram_api_key: str = Field(alias="DEEPGRAM_API_KEY")
    source_id: str = Field(alias="SOURCE_ID")
    source_type: str = Field(alias="SOURCE_TYPE")
    device_owner: str | None = Field(default=None, alias="DEVICE_OWNER")
    spool_dir: str = Field(alias="SPOOL_DIR")
    active_start_local: str = Field(alias="ACTIVE_START_LOCAL")
    active_end_local: str = Field(alias="ACTIVE_END_LOCAL")

    model_config = COMMON_MODEL_CONFIG
