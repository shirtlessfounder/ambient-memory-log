from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import importlib
from typing import Any

import pytest


def _import_pipeline_module():
    try:
        return importlib.import_module("ambient_memory.pipeline.room_enrichment")
    except ModuleNotFoundError as exc:
        pytest.fail(f"room enrichment module missing: {exc}")


def _import_client_module():
    try:
        return importlib.import_module("ambient_memory.integrations.openai_room_enrichment_client")
    except ModuleNotFoundError as exc:
        pytest.fail(f"OpenAI room enrichment client module missing: {exc}")


class FakeTransport:
    def __init__(self, *, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": deepcopy(headers) if headers is not None else {},
                "payload": deepcopy(payload) if payload is not None else None,
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return deepcopy(self.responses.pop(0))


def test_openai_room_enrichment_client_resolves_speakers_from_structured_output() -> None:
    pipeline = _import_pipeline_module()
    client_module = _import_client_module()
    transport = FakeTransport(
        responses=[
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"utterances": ['
                                '{"utterance_index": 0, "resolved_speaker_name": "Dylan", '
                                '"resolved_speaker_confidence": 0.94, "resolution_notes": "named turn"},'
                                '{"utterance_index": 1, "resolved_speaker_name": "unknown", '
                                '"resolved_speaker_confidence": 0.44, "resolution_notes": "insufficient evidence"}'
                                "]}"
                            )
                        }
                    }
                ]
            }
        ]
    )
    client = client_module.OpenAIRoomEnrichmentClient(
        api_key="openai-secret",
        transport=transport,
        model="gpt-5.4-mini",
    )
    utterances = [
        pipeline.RoomEnrichmentUtterance(
            canonical_utterance_id="utt-1",
            started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
            ended_at=datetime(2026, 4, 10, 13, 0, 5, tzinfo=UTC),
            raw_text="ship it after lunch",
            current_speaker_label="A",
        ),
        pipeline.RoomEnrichmentUtterance(
            canonical_utterance_id="utt-2",
            started_at=datetime(2026, 4, 10, 13, 1, tzinfo=UTC),
            ended_at=datetime(2026, 4, 10, 13, 1, 5, tzinfo=UTC),
            raw_text="i can take the follow up",
            current_speaker_label=None,
        ),
    ]

    rows = client.resolve_speakers(
        utterances,
        allowed_speakers=("Dylan", "Niyant", "Alex", "Jakub", "unknown"),
    )

    assert [row.canonical_utterance_id for row in rows] == ["utt-1", "utt-2"]
    assert [row.resolved_speaker_name for row in rows] == ["Dylan", "unknown"]
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer openai-secret"
    assert transport.calls[0]["payload"]["model"] == "gpt-5.4-mini"
    assert transport.calls[0]["payload"]["response_format"]["type"] == "json_schema"
    assert "Dylan" in transport.calls[0]["payload"]["messages"][1]["content"]
    assert "utt-1" in transport.calls[0]["payload"]["messages"][1]["content"]


def test_openai_room_enrichment_client_cleans_text_from_structured_output() -> None:
    pipeline = _import_pipeline_module()
    client_module = _import_client_module()
    transport = FakeTransport(
        responses=[
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"utterances": ['
                                '{"utterance_index": 0, "cleaned_text": "Ship it after lunch.", '
                                '"cleaned_text_confidence": 0.82},'
                                '{"utterance_index": 1, "cleaned_text": "I can take the follow-up.", '
                                '"cleaned_text_confidence": 0.73}'
                                "]}"
                            )
                        }
                    }
                ]
            }
        ]
    )
    client = client_module.OpenAIRoomEnrichmentClient(
        api_key="openai-secret",
        transport=transport,
        model="gpt-5.4-mini",
    )
    utterances = [
        pipeline.RoomEnrichmentUtterance(
            canonical_utterance_id="utt-1",
            started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
            ended_at=datetime(2026, 4, 10, 13, 0, 5, tzinfo=UTC),
            raw_text="ship it after lunch",
            current_speaker_label="A",
        ),
        pipeline.RoomEnrichmentUtterance(
            canonical_utterance_id="utt-2",
            started_at=datetime(2026, 4, 10, 13, 1, tzinfo=UTC),
            ended_at=datetime(2026, 4, 10, 13, 1, 5, tzinfo=UTC),
            raw_text="i can take the follow up",
            current_speaker_label=None,
        ),
    ]
    speaker_resolutions = [
        pipeline.RoomEnrichmentSpeakerResolution(
            canonical_utterance_id="utt-1",
            resolved_speaker_name="Dylan",
            resolved_speaker_confidence=0.94,
            resolution_notes="named turn",
        ),
        pipeline.RoomEnrichmentSpeakerResolution(
            canonical_utterance_id="utt-2",
            resolved_speaker_name="unknown",
            resolved_speaker_confidence=0.44,
            resolution_notes="insufficient evidence",
        ),
    ]

    rows = client.cleanup_text(utterances, speaker_resolutions=speaker_resolutions)

    assert [row.cleaned_text for row in rows] == [
        "Ship it after lunch.",
        "I can take the follow-up.",
    ]
    assert transport.calls[0]["payload"]["response_format"]["json_schema"]["name"] == "room_text_cleanup"
    assert "unknown" in transport.calls[0]["payload"]["messages"][1]["content"]


def test_openai_room_enrichment_client_rejects_index_mismatch() -> None:
    pipeline = _import_pipeline_module()
    client_module = _import_client_module()
    transport = FakeTransport(
        responses=[
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"utterances": ['
                                '{"utterance_index": 0, "resolved_speaker_name": "Dylan", '
                                '"resolved_speaker_confidence": 0.94, "resolution_notes": "named turn"}'
                                "]}"
                            )
                        }
                    }
                ]
            }
        ]
    )
    client = client_module.OpenAIRoomEnrichmentClient(
        api_key="openai-secret",
        transport=transport,
        model="gpt-5.4-mini",
    )
    utterances = [
        pipeline.RoomEnrichmentUtterance(
            canonical_utterance_id="utt-1",
            started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
            ended_at=datetime(2026, 4, 10, 13, 0, 5, tzinfo=UTC),
            raw_text="ship it after lunch",
            current_speaker_label="A",
        ),
        pipeline.RoomEnrichmentUtterance(
            canonical_utterance_id="utt-2",
            started_at=datetime(2026, 4, 10, 13, 1, tzinfo=UTC),
            ended_at=datetime(2026, 4, 10, 13, 1, 5, tzinfo=UTC),
            raw_text="i can take the follow up",
            current_speaker_label=None,
        ),
    ]

    with pytest.raises(client_module.OpenAIRoomEnrichmentClientError, match="row-preserving"):
        client.resolve_speakers(
            utterances,
            allowed_speakers=("Dylan", "Niyant", "Alex", "Jakub", "unknown"),
        )


def test_openai_room_enrichment_client_maps_rows_by_index_instead_of_uuid_echo() -> None:
    pipeline = _import_pipeline_module()
    client_module = _import_client_module()
    transport = FakeTransport(
        responses=[
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"utterances": ['
                                '{"utterance_index": 0, "resolved_speaker_name": "Dylan", '
                                '"resolved_speaker_confidence": 0.94, "resolution_notes": "named turn"},'
                                '{"utterance_index": 1, "resolved_speaker_name": "unknown", '
                                '"resolved_speaker_confidence": 0.44, "resolution_notes": "insufficient evidence"}'
                                "]}"
                            )
                        }
                    }
                ]
            }
        ]
    )
    client = client_module.OpenAIRoomEnrichmentClient(
        api_key="openai-secret",
        transport=transport,
        model="gpt-5.4-mini",
    )
    utterances = [
        pipeline.RoomEnrichmentUtterance(
            canonical_utterance_id="very-long-uuid-like-id-1",
            started_at=datetime(2026, 4, 10, 13, 0, tzinfo=UTC),
            ended_at=datetime(2026, 4, 10, 13, 0, 5, tzinfo=UTC),
            raw_text="ship it after lunch",
            current_speaker_label="A",
        ),
        pipeline.RoomEnrichmentUtterance(
            canonical_utterance_id="very-long-uuid-like-id-2",
            started_at=datetime(2026, 4, 10, 13, 1, tzinfo=UTC),
            ended_at=datetime(2026, 4, 10, 13, 1, 5, tzinfo=UTC),
            raw_text="i can take the follow up",
            current_speaker_label=None,
        ),
    ]

    rows = client.resolve_speakers(
        utterances,
        allowed_speakers=("Dylan", "Niyant", "Alex", "Jakub", "unknown"),
    )

    assert [row.canonical_utterance_id for row in rows] == [
        "very-long-uuid-like-id-1",
        "very-long-uuid-like-id-2",
    ]
