from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any

import pytest

from ambient_memory.integrations.assemblyai_client import AssemblyAIClient, AssemblyAIClientError


class FakeTransport:
    def __init__(
        self,
        *,
        upload_response: dict[str, Any] | None = None,
        transcript_responses: list[dict[str, Any]] | None = None,
    ) -> None:
        self.upload_response = upload_response or {"upload_url": "https://cdn.assembly.test/upload/audio.wav"}
        self.transcript_responses = deque(transcript_responses or [])
        self.request_calls: list[dict[str, Any]] = []
        self.upload_calls: list[dict[str, Any]] = []

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        self.request_calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "payload": deepcopy(payload),
                "timeout": timeout,
            }
        )
        if not self.transcript_responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return deepcopy(self.transcript_responses.popleft())

    def upload_bytes(
        self,
        url: str,
        *,
        data: bytes,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        self.upload_calls.append(
            {
                "url": url,
                "data": data,
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        return deepcopy(self.upload_response)


def test_transcribe_bytes_uploads_audio_submits_job_polls_and_parses_utterances() -> None:
    transport = FakeTransport(
        transcript_responses=[
            {"id": "tx-123", "status": "queued"},
            {"id": "tx-123", "status": "processing"},
            {
                "id": "tx-123",
                "status": "completed",
                "speech_understanding": {
                    "response": {
                        "speaker_identification": {
                            "mapping": {
                                "A": "Dylan",
                                "B": "Niyant",
                            }
                        }
                    }
                },
                "utterances": [
                    {
                        "id": "utt-1",
                        "speaker": "Dylan",
                        "start": 1500,
                        "end": 3250,
                        "confidence": 0.98,
                        "text": "Hello room.",
                    },
                    {
                        "id": "utt-2",
                        "speaker": "C",
                        "start": 5000,
                        "end": 7000,
                        "confidence": 0.93,
                        "text": "Unknown speaker turn.",
                    },
                ],
            },
        ]
    )
    client = AssemblyAIClient(
        api_key="assembly-secret",
        transport=transport,
        poll_interval_seconds=0,
    )

    utterances = client.transcribe_bytes(
        b"audio-bytes",
        speaker_names=("Dylan", "Niyant", "Alex", "Jakub"),
    )

    assert len(transport.upload_calls) == 1
    assert transport.upload_calls[0]["url"] == "https://api.assemblyai.com/v2/upload"
    assert transport.upload_calls[0]["data"] == b"audio-bytes"
    assert transport.upload_calls[0]["headers"]["Authorization"] == "assembly-secret"
    assert transport.upload_calls[0]["headers"]["Content-Type"] == "application/octet-stream"

    assert [call["method"] for call in transport.request_calls] == ["POST", "GET", "GET"]
    assert transport.request_calls[0]["url"] == "https://api.assemblyai.com/v2/transcript"
    assert transport.request_calls[1]["url"] == "https://api.assemblyai.com/v2/transcript/tx-123"
    assert transport.request_calls[2]["url"] == "https://api.assemblyai.com/v2/transcript/tx-123"
    assert transport.request_calls[0]["payload"] == {
        "audio_url": "https://cdn.assembly.test/upload/audio.wav",
        "language_detection": True,
        "speaker_labels": True,
        "speakers_expected": 4,
        "speech_models": ["universal-3-pro", "universal-2"],
        "speech_understanding": {
            "request": {
                "speaker_identification": {
                    "speaker_type": "name",
                    "known_values": ["Dylan", "Niyant", "Alex", "Jakub"],
                }
            }
        },
    }

    assert utterances[0].vendor_segment_id == "utt-1"
    assert utterances[0].text == "Hello room."
    assert utterances[0].speaker_hint == "A"
    assert utterances[0].speaker_name == "Dylan"
    assert utterances[0].confidence == 0.98
    assert utterances[0].start_seconds == 1.5
    assert utterances[0].end_seconds == 3.25
    assert utterances[0].raw_payload["speaker"] == "Dylan"

    assert utterances[1].vendor_segment_id == "utt-2"
    assert utterances[1].text == "Unknown speaker turn."
    assert utterances[1].speaker_hint == "C"
    assert utterances[1].speaker_name is None
    assert utterances[1].confidence == 0.93
    assert utterances[1].start_seconds == 5.0
    assert utterances[1].end_seconds == 7.0


def test_transcribe_bytes_raises_on_transcript_error_status() -> None:
    transport = FakeTransport(
        transcript_responses=[
            {"id": "tx-123", "status": "queued"},
            {"id": "tx-123", "status": "error", "error": "unsupported audio"},
        ]
    )
    client = AssemblyAIClient(
        api_key="assembly-secret",
        transport=transport,
        poll_interval_seconds=0,
    )

    with pytest.raises(AssemblyAIClientError, match="unsupported audio"):
        client.transcribe_bytes(
            b"audio-bytes",
            speaker_names=("Dylan", "Niyant", "Alex", "Jakub"),
        )


def test_transcribe_bytes_raises_on_malformed_completed_payload() -> None:
    transport = FakeTransport(
        transcript_responses=[
            {"id": "tx-123", "status": "queued"},
            {"id": "tx-123", "status": "completed", "utterances": "not-a-list"},
        ]
    )
    client = AssemblyAIClient(
        api_key="assembly-secret",
        transport=transport,
        poll_interval_seconds=0,
    )

    with pytest.raises(AssemblyAIClientError, match="utterances"):
        client.transcribe_bytes(
            b"audio-bytes",
            speaker_names=("Dylan", "Niyant", "Alex", "Jakub"),
        )
