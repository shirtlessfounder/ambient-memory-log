from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


COMMON_MODEL_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    populate_by_name=True,
    extra="ignore",
)


class EnrollmentSettings(BaseSettings):
    database_url: str = Field(alias="DATABASE_URL")
    database_ssl_root_cert: str | None = Field(default=None, alias="DATABASE_SSL_ROOT_CERT")
    pyannote_api_key: str = Field(alias="PYANNOTE_API_KEY")

    model_config = COMMON_MODEL_CONFIG


class WorkerSettings(BaseSettings):
    database_url: str = Field(alias="DATABASE_URL")
    database_ssl_root_cert: str | None = Field(default=None, alias="DATABASE_SSL_ROOT_CERT")
    aws_region: str | None = Field(default=None, alias="AWS_REGION")
    deepgram_api_key: str | None = Field(default=None, alias="DEEPGRAM_API_KEY")
    pyannote_api_key: str | None = Field(default=None, alias="PYANNOTE_API_KEY")

    model_config = COMMON_MODEL_CONFIG


class ApiSettings(BaseSettings):
    database_url: str = Field(alias="DATABASE_URL")
    database_ssl_root_cert: str | None = Field(default=None, alias="DATABASE_SSL_ROOT_CERT")
    aws_region: str | None = Field(default=None, alias="AWS_REGION")
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_presign_expires_in: int = Field(default=3600, alias="API_PRESIGN_EXPIRES_IN")

    model_config = COMMON_MODEL_CONFIG


class Settings(EnrollmentSettings):
    aws_region: str = Field(alias="AWS_REGION")
    s3_bucket: str = Field(alias="S3_BUCKET")
    deepgram_api_key: str = Field(alias="DEEPGRAM_API_KEY")
    source_id: str = Field(alias="SOURCE_ID")
    source_type: str = Field(alias="SOURCE_TYPE")
    device_owner: str | None = Field(default=None, alias="DEVICE_OWNER")
    spool_dir: str = Field(alias="SPOOL_DIR")
    active_start_local: str = Field(alias="ACTIVE_START_LOCAL")
    active_end_local: str = Field(alias="ACTIVE_END_LOCAL")

    model_config = COMMON_MODEL_CONFIG
