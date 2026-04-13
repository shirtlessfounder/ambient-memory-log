from __future__ import annotations

from datetime import UTC, datetime
import importlib
from urllib.error import URLError
from urllib.request import Request

import pytest


def _import_client_module():
    try:
        return importlib.import_module("ambient_memory.integrations.openai_room_retranscription_client")
    except ModuleNotFoundError as exc:
        pytest.fail(f"OpenAI room retranscription client module missing: {exc}")


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.closed = False

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, *, responses: list[bytes] | None = None, error: Exception | None = None) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.requests: list[Request] = []

    def __call__(self, http_request: Request) -> FakeResponse:
        self.requests.append(http_request)
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError(f"unexpected request: {http_request.get_method()} {http_request.full_url}")
        return FakeResponse(self.responses.pop(0))


def test_openai_room_retranscription_client_posts_multipart_diarized_request_and_parses_segments() -> None:
    client_module = _import_client_module()
    transport = FakeTransport(
        responses=[
            (
                b'{"segments": ['
                b'{"start": 0.25, "end": 1.75, "text": "Ship it after lunch.", "confidence": 0.93},'
                b'{"start": 2.10, "end": 3.40, "text": "I can take the follow-up.", "confidence": 0.81}'
                b"]}"
            )
        ]
    )
    client = client_module.OpenAIRoomRetranscriptionClient(
        api_key="openai-secret",
        transport=transport,
    )

    segments = client.transcribe_window(
        audio_bytes=b"RIFF-fake-window-audio",
        filename="room-window.wav",
        window_started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
    )

    assert segments == [
        client_module.RoomRetranscribedSegment(
            start_seconds=0.25,
            end_seconds=1.75,
            text="Ship it after lunch.",
            confidence=0.93,
        ),
        client_module.RoomRetranscribedSegment(
            start_seconds=2.10,
            end_seconds=3.40,
            text="I can take the follow-up.",
            confidence=0.81,
        ),
    ]

    request = transport.requests[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.get_method() == "POST"
    assert request.full_url == "https://api.openai.com/v1/audio/transcriptions"
    assert headers["authorization"] == "Bearer openai-secret"
    assert headers["accept"] == "application/json"
    assert "multipart/form-data; boundary=" in headers["content-type"]

    body = request.data.decode("utf-8", errors="replace")
    assert 'name="file"; filename="room-window.wav"' in body
    assert "RIFF-fake-window-audio" in body
    assert 'name="model"' in body
    assert "gpt-4o-transcribe-diarize" in body
    assert 'name="response_format"' in body
    assert "diarized_json" in body


def test_openai_room_retranscription_client_posts_chunking_strategy_for_diarization_model() -> None:
    client_module = _import_client_module()
    transport = FakeTransport(responses=[b'{"segments": []}'])
    client = client_module.OpenAIRoomRetranscriptionClient(
        api_key="openai-secret",
        transport=transport,
    )

    client.transcribe_window(
        audio_bytes=b"RIFF-fake-window-audio",
        filename="room-window.wav",
        window_started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
    )

    body = transport.requests[0].data.decode("utf-8", errors="replace")
    assert 'name="chunking_strategy"' in body
    assert "auto" in body


def test_openai_room_retranscription_client_raises_on_transport_failure() -> None:
    client_module = _import_client_module()
    client = client_module.OpenAIRoomRetranscriptionClient(
        api_key="openai-secret",
        transport=FakeTransport(error=URLError("connection reset by peer")),
    )

    with pytest.raises(client_module.OpenAIRoomRetranscriptionClientError, match="connection reset by peer"):
        client.transcribe_window(
            audio_bytes=b"audio-bytes",
            filename="room-window.wav",
            window_started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
        )


def test_openai_room_retranscription_client_raises_on_timeout() -> None:
    client_module = _import_client_module()

    class TimeoutTransport:
        def __call__(self, http_request: Request) -> FakeResponse:
            raise TimeoutError("The read operation timed out")

    client = client_module.OpenAIRoomRetranscriptionClient(
        api_key="openai-secret",
        transport=TimeoutTransport(),
    )

    with pytest.raises(client_module.OpenAIRoomRetranscriptionClientError, match="timed out"):
        client.transcribe_window(
            audio_bytes=b"audio-bytes",
            filename="room-window.wav",
            window_started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
        )


def test_openai_room_retranscription_client_uses_configured_timeout_for_default_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_module = _import_client_module()
    seen: dict[str, object] = {}

    def fake_urlopen(http_request: Request, *, timeout: float) -> FakeResponse:
        seen["request"] = http_request
        seen["timeout"] = timeout
        return FakeResponse(b'{"segments": []}')

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)
    client = client_module.OpenAIRoomRetranscriptionClient(
        api_key="openai-secret",
        timeout=321.0,
    )

    segments = client.transcribe_window(
        audio_bytes=b"audio-bytes",
        filename="room-window.wav",
        window_started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
    )

    assert segments == []
    assert seen["timeout"] == 321.0


def test_openai_room_retranscription_client_raises_on_invalid_json_response() -> None:
    client_module = _import_client_module()
    client = client_module.OpenAIRoomRetranscriptionClient(
        api_key="openai-secret",
        transport=FakeTransport(responses=[b"{not-json"]),
    )

    with pytest.raises(client_module.OpenAIRoomRetranscriptionClientError, match="valid JSON"):
        client.transcribe_window(
            audio_bytes=b"audio-bytes",
            filename="room-window.wav",
            window_started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
        )


def test_openai_room_retranscription_client_raises_on_malformed_segment_payload() -> None:
    client_module = _import_client_module()
    client = client_module.OpenAIRoomRetranscriptionClient(
        api_key="openai-secret",
        transport=FakeTransport(
            responses=[
                b'{"segments": [{"start": 4.0, "end": 3.5, "text": "bad bounds", "confidence": 0.9}]}'
            ]
        ),
    )

    with pytest.raises(client_module.OpenAIRoomRetranscriptionClientError, match="segment end"):
        client.transcribe_window(
            audio_bytes=b"audio-bytes",
            filename="room-window.wav",
            window_started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
        )


def test_openai_room_retranscription_client_skips_empty_text_segments() -> None:
    client_module = _import_client_module()
    client = client_module.OpenAIRoomRetranscriptionClient(
        api_key="openai-secret",
        transport=FakeTransport(
            responses=[
                (
                    b'{"segments": ['
                    b'{"start": 0.0, "end": 0.5, "text": "   ", "confidence": 0.9},'
                    b'{"start": 0.5, "end": 1.5, "text": "Ship it after lunch.", "confidence": 0.81}'
                    b"]}"
                )
            ]
        ),
    )

    segments = client.transcribe_window(
        audio_bytes=b"audio-bytes",
        filename="room-window.wav",
        window_started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
    )

    assert segments == [
        client_module.RoomRetranscribedSegment(
            start_seconds=0.5,
            end_seconds=1.5,
            text="Ship it after lunch.",
            confidence=0.81,
        )
    ]
