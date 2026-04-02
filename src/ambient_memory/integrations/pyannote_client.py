from __future__ import annotations

from collections.abc import Mapping
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request
from uuid import uuid4


class PyannoteError(RuntimeError):
    pass


class Transport(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]: ...

    def upload_bytes(
        self,
        url: str,
        *,
        data: bytes,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None: ...


class StdlibTransport:
    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        request_headers = dict(headers or {})
        data: bytes | None = None

        if payload is not None:
            request_headers.setdefault("Content-Type", "application/json")
            data = json.dumps(payload).encode("utf-8")

        http_request = request.Request(url, data=data, headers=request_headers, method=method.upper())

        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                body = response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise PyannoteError(f"pyannote request failed: {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise PyannoteError(f"pyannote request failed: {exc.reason}") from exc

        if not body:
            return {}

        return json.loads(body.decode("utf-8"))

    def upload_bytes(
        self,
        url: str,
        *,
        data: bytes,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        request_headers = {"Content-Type": "application/octet-stream", **(headers or {})}
        http_request = request.Request(url, data=data, headers=request_headers, method="PUT")

        try:
            with request.urlopen(http_request, timeout=timeout):
                return
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise PyannoteError(f"pyannote upload failed: {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise PyannoteError(f"pyannote upload failed: {exc.reason}") from exc


@dataclass(frozen=True, slots=True)
class VoiceprintReference:
    label: str
    voiceprint: str


@dataclass(frozen=True, slots=True)
class IdentificationMatch:
    speaker: str
    match: str | None
    confidence: dict[str, float]
    start_seconds: float | None = None
    end_seconds: float | None = None


class PyannoteClient:
    def __init__(
        self,
        *,
        api_key: str,
        transport: Transport | None = None,
        base_url: str = "https://api.pyannote.ai/v1",
        poll_interval_seconds: float = 1.0,
        max_poll_attempts: int = 60,
    ) -> None:
        self.api_key = api_key
        self.transport = transport or StdlibTransport()
        self.base_url = base_url.rstrip("/")
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_attempts = max_poll_attempts

    def enroll_voiceprint(
        self,
        *,
        label: str,
        audio_bytes: bytes,
        filename: str,
    ) -> str:
        media_url = self._upload_media(audio_bytes=audio_bytes, filename=filename, prefix="voiceprints", hint=label)
        job = self.transport.request_json(
            "POST",
            f"{self.base_url}/voiceprint",
            headers=self._headers(),
            payload={"url": media_url},
        )
        result = self.wait_for_job(str(job["jobId"]))
        voiceprint = result.get("output", {}).get("voiceprint")

        if not voiceprint:
            raise PyannoteError("pyannote voiceprint job succeeded without a voiceprint payload")

        return str(voiceprint)

    def identify_speakers(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        voiceprints: list[VoiceprintReference],
        matching_threshold: int = 0,
        exclusive: bool = True,
    ) -> list[IdentificationMatch]:
        media_url = self._upload_media(audio_bytes=audio_bytes, filename=filename, prefix="identify")
        job = self.transport.request_json(
            "POST",
            f"{self.base_url}/identify",
            headers=self._headers(),
            payload={
                "url": media_url,
                "voiceprints": [
                    {"label": voiceprint.label, "voiceprint": voiceprint.voiceprint}
                    for voiceprint in voiceprints
                ],
                "confidence": True,
                "matching": {
                    "threshold": matching_threshold,
                    "exclusive": exclusive,
                },
            },
        )
        result = self.wait_for_job(str(job["jobId"]))

        matches: list[IdentificationMatch] = []
        for item in result.get("output", {}).get("voiceprints", []):
            confidence = {
                str(speaker): float(score)
                for speaker, score in dict(item.get("confidence") or {}).items()
            }
            start_seconds, end_seconds = _parse_segment_bounds(item)
            matches.append(
                IdentificationMatch(
                    speaker=str(item.get("speaker", "")),
                    match=item.get("match"),
                    confidence=confidence,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            )

        return matches

    def wait_for_job(self, job_id: str) -> dict[str, Any]:
        for attempt in range(self.max_poll_attempts):
            job = self.transport.request_json(
                "GET",
                f"{self.base_url}/jobs/{job_id}",
                headers=self._headers(),
            )
            status = str(job.get("status", ""))

            if status == "succeeded":
                return job
            if status in {"failed", "canceled"}:
                raise PyannoteError(f"pyannote job {job_id} ended with status {status}")
            if attempt + 1 < self.max_poll_attempts:
                time.sleep(self.poll_interval_seconds)

        raise PyannoteError(f"pyannote job {job_id} did not complete after {self.max_poll_attempts} polls")

    def _upload_media(self, *, audio_bytes: bytes, filename: str, prefix: str, hint: str | None = None) -> str:
        key_hint = filename if hint is None else f"{hint}-{filename}"
        media_url = f"media://{prefix}/{uuid4().hex}-{_sanitize_key(key_hint)}"
        response = self.transport.request_json(
            "POST",
            f"{self.base_url}/media/input",
            headers=self._headers(),
            payload={"url": media_url},
        )
        upload_url = str(response["url"])
        self.transport.upload_bytes(upload_url, data=audio_bytes)
        return media_url

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


def _sanitize_key(value: str) -> str:
    allowed = []
    for character in value.strip().replace(" ", "-"):
        if character.isalnum() or character in {"-", "_", ".", "/"}:
            allowed.append(character)
        else:
            allowed.append("-")

    sanitized = "".join(allowed).strip("-")
    return sanitized or "audio"


def _parse_segment_bounds(item: Mapping[str, Any]) -> tuple[float | None, float | None]:
    for candidate in (item, item.get("segment"), item.get("turn")):
        if not isinstance(candidate, Mapping):
            continue

        start_seconds = _coerce_float(
            candidate.get("start_seconds", candidate.get("startTime", candidate.get("start")))
        )
        end_seconds = _coerce_float(
            candidate.get("end_seconds", candidate.get("endTime", candidate.get("end")))
        )
        if start_seconds is not None and end_seconds is not None:
            return start_seconds, end_seconds

    return None, None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)

    try:
        return float(str(value))
    except ValueError:
        return None
