from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping


def build_chunk_key(source_id: str, started_at: datetime, *, extension: str = "wav") -> str:
    normalized_started_at = _normalize_timestamp(started_at)
    normalized_extension = extension.lstrip(".") or "wav"
    object_name = normalized_started_at.strftime("%Y%m%dT%H%M%SZ")

    return (
        f"raw-audio/{source_id}/{normalized_started_at:%Y/%m/%d}/"
        f"{object_name}.{normalized_extension}"
    )


def upload_chunk(
    *,
    client: Any,
    bucket: str,
    source_id: str,
    started_at: datetime,
    body: Any,
    extension: str = "wav",
    content_type: str = "audio/wav",
    metadata: Mapping[str, str] | None = None,
) -> str:
    key = build_chunk_key(source_id, started_at, extension=extension)
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
        Metadata=dict(metadata or {}),
    )
    return key


def presign_chunk_url(*, client: Any, bucket: str, key: str, expires_in: int = 3600) -> str:
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
