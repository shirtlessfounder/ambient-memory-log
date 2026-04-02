from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(alias="DATABASE_URL")
    database_ssl_root_cert: str | None = Field(default=None, alias="DATABASE_SSL_ROOT_CERT")
    aws_region: str = Field(alias="AWS_REGION")
    s3_bucket: str = Field(alias="S3_BUCKET")
    deepgram_api_key: str = Field(alias="DEEPGRAM_API_KEY")
    pyannote_api_key: str = Field(alias="PYANNOTE_API_KEY")
    source_id: str = Field(alias="SOURCE_ID")
    source_type: str = Field(alias="SOURCE_TYPE")
    device_owner: str | None = Field(default=None, alias="DEVICE_OWNER")
    spool_dir: str = Field(alias="SPOOL_DIR")
    active_start_local: str = Field(alias="ACTIVE_START_LOCAL")
    active_end_local: str = Field(alias="ACTIVE_END_LOCAL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )
