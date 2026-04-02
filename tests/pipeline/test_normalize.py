from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest


SAMPLE_DEEPGRAM_PAYLOAD = {
    "metadata": {
        "request_id": "req-123",
    },
    "results": {
        "channels": [
            {
                "alternatives": [
                    {
                        "transcript": "Hello there. General Kenobi.",
                        "confidence": 0.97,
                    }
                ]
            }
        ],
        "utterances": [
            {
                "id": "utt-1",
                "start": 0.5,
                "end": 1.9,
                "confidence": 0.98,
                "channel": 0,
                "speaker": 0,
                "transcript": "Hello there.",
                "words": [
                    {
                        "word": "hello",
                        "start": 0.5,
                        "end": 0.9,
                        "confidence": 0.99,
                        "speaker": 0,
                        "speaker_confidence": 0.9,
                        "punctuated_word": "Hello",
                    },
                    {
                        "word": "there",
                        "start": 1.1,
                        "end": 1.9,
                        "confidence": 0.97,
                        "speaker": 0,
                        "speaker_confidence": 0.8,
                        "punctuated_word": "there.",
                    },
                ],
            },
            {
                "id": "utt-2",
                "start": 2.0,
                "end": 3.25,
                "confidence": 0.94,
                "channel": 0,
                "speaker": 1,
                "transcript": "General Kenobi.",
                "words": [
                    {
                        "word": "general",
                        "start": 2.0,
                        "end": 2.6,
                        "confidence": 0.95,
                        "speaker": 1,
                        "speaker_confidence": 0.7,
                        "punctuated_word": "General",
                    },
                    {
                        "word": "kenobi",
                        "start": 2.7,
                        "end": 3.25,
                        "confidence": 0.93,
                        "speaker": 1,
                        "speaker_confidence": 0.9,
                        "punctuated_word": "Kenobi.",
                    },
                ],
            },
        ],
    },
}


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.closed = False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def close(self) -> None:
        self.closed = True


class RecordingTransport:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {"results": {}}
        self.requests: list[Any] = []
        self.responses: list[FakeHTTPResponse] = []

    def __call__(self, request: Any) -> FakeHTTPResponse:
        self.requests.append(request)
        response = FakeHTTPResponse(self.payload)
        self.responses.append(response)
        return response


def load_deepgram_client_module() -> Any:
    try:
        module = importlib.import_module("ambient_memory.integrations.deepgram_client")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing deepgram client module: {exc}")

    if not hasattr(module, "DeepgramClient"):
        pytest.fail("missing deepgram client symbol: DeepgramClient")

    return module


def load_normalize_module() -> Any:
    try:
        module = importlib.import_module("ambient_memory.pipeline.normalize")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing normalize module: {exc}")

    if not hasattr(module, "NormalizedTranscriptSegment"):
        pytest.fail("missing normalize symbol: NormalizedTranscriptSegment")

    if not hasattr(module, "normalize_deepgram_response"):
        pytest.fail("missing normalize function: normalize_deepgram_response")

    return module


def test_deepgram_client_transcribe_bytes_requests_diarized_utterances() -> None:
    module = load_deepgram_client_module()
    transport = RecordingTransport(payload=SAMPLE_DEEPGRAM_PAYLOAD)
    client = module.DeepgramClient(api_key="test-key", transport=transport)

    response = client.transcribe_bytes(b"audio-bytes", content_type="audio/wav")

    assert response == SAMPLE_DEEPGRAM_PAYLOAD
    assert len(transport.requests) == 1
    request = transport.requests[0]
    query = parse_qs(urlsplit(request.full_url).query)

    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Token test-key"
    assert request.get_header("Content-type") == "audio/wav"
    assert request.data == b"audio-bytes"
    assert query["model"] == ["nova-3"]
    assert query["diarize"] == ["true"]
    assert query["utterances"] == ["true"]
    assert query["punctuate"] == ["true"]
    assert query["smart_format"] == ["true"]
    assert transport.responses[0].closed is True


def test_deepgram_client_transcribe_url_posts_remote_source() -> None:
    module = load_deepgram_client_module()
    transport = RecordingTransport(payload=SAMPLE_DEEPGRAM_PAYLOAD)
    client = module.DeepgramClient(api_key="test-key", transport=transport)

    response = client.transcribe_url("https://example.test/audio.wav")

    assert response == SAMPLE_DEEPGRAM_PAYLOAD
    assert len(transport.requests) == 1
    request = transport.requests[0]

    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Token test-key"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data.decode("utf-8")) == {"url": "https://example.test/audio.wav"}


def test_normalize_deepgram_response_produces_segments() -> None:
    module = load_normalize_module()
    chunk_started_at = datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC)

    segments = module.normalize_deepgram_response(
        SAMPLE_DEEPGRAM_PAYLOAD,
        source_id="desk-a",
        chunk_started_at=chunk_started_at,
    )

    assert len(segments) == 2

    first = segments[0]
    assert isinstance(first, module.NormalizedTranscriptSegment)
    assert first.source_id == "desk-a"
    assert first.vendor == "deepgram"
    assert first.vendor_segment_id == "utt-1"
    assert first.text == "Hello there."
    assert first.speaker_hint == "speaker_0"
    assert first.speaker_confidence == pytest.approx(0.85)
    assert first.confidence == pytest.approx(0.98)
    assert first.started_at == chunk_started_at + timedelta(seconds=0.5)
    assert first.ended_at == chunk_started_at + timedelta(seconds=1.9)
    assert first.raw_payload == SAMPLE_DEEPGRAM_PAYLOAD["results"]["utterances"][0]

    second = segments[1]
    assert second.vendor_segment_id == "utt-2"
    assert second.text == "General Kenobi."
    assert second.speaker_hint == "speaker_1"
    assert second.speaker_confidence == pytest.approx(0.8)
    assert second.started_at == chunk_started_at + timedelta(seconds=2.0)
    assert second.ended_at == chunk_started_at + timedelta(seconds=3.25)


def test_normalize_deepgram_response_handles_empty_payloads() -> None:
    module = load_normalize_module()
    chunk_started_at = datetime(2026, 4, 2, 13, 0, 0, tzinfo=UTC)

    assert module.normalize_deepgram_response({}, source_id="desk-a", chunk_started_at=chunk_started_at) == []
    assert module.normalize_deepgram_response(
        {
            "results": {
                "channels": [{"alternatives": [{"transcript": ""}]}],
                "utterances": [],
            }
        },
        source_id="desk-a",
        chunk_started_at=chunk_started_at,
    ) == []
