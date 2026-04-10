from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Any

import pytest

from ambient_memory.integrations.pyannote_client import IdentificationMatch, VoiceprintReference


def _import_room_track_identity_module():
    try:
        return importlib.import_module("ambient_memory.pipeline.room_track_identity")
    except ModuleNotFoundError as exc:
        pytest.fail(f"room track identity module missing: {exc}")


@dataclass(frozen=True, slots=True)
class RoomTrackBundle:
    raw_track_label: str
    audio_bytes: bytes
    speech_seconds: float


class FakePyannoteClient:
    def __init__(self, matches: dict[bytes, list[IdentificationMatch]]) -> None:
        self.matches = matches
        self.calls: list[dict[str, Any]] = []

    def identify_speakers(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        voiceprints: list[VoiceprintReference],
        matching_threshold: int = 0,
        exclusive: bool = True,
    ) -> list[IdentificationMatch]:
        self.calls.append(
            {
                "audio_bytes": audio_bytes,
                "filename": filename,
                "voiceprints": voiceprints,
                "matching_threshold": matching_threshold,
                "exclusive": exclusive,
            }
        )
        return self.matches[audio_bytes]


VOICEPRINTS = (
    VoiceprintReference(label="Dylan", voiceprint="vp-dylan"),
    VoiceprintReference(label="Alex", voiceprint="vp-alex"),
    VoiceprintReference(label="Niyant", voiceprint="vp-niyant"),
)


def test_resolve_track_identities_assigns_teammate_when_threshold_and_margin_clear() -> None:
    room_track_identity = _import_room_track_identity_module()
    bundle = RoomTrackBundle(raw_track_label="A", audio_bytes=b"track-a", speech_seconds=12.5)
    pyannote_client = FakePyannoteClient(
        {
            b"track-a": [
                IdentificationMatch(
                    speaker="speaker-a",
                    match="Dylan",
                    confidence={"Dylan": 0.93, "Alex": 0.61, "Niyant": 0.14},
                )
            ]
        }
    )

    resolved = room_track_identity.resolve_track_identities(
        track_bundles=(bundle,),
        pyannote_client=pyannote_client,
        voiceprints=VOICEPRINTS,
    )

    assert len(pyannote_client.calls) == 1
    assert pyannote_client.calls[0]["audio_bytes"] == b"track-a"
    assert pyannote_client.calls[0]["filename"] == "room-track-A.wav"
    assert pyannote_client.calls[0]["voiceprints"] == list(VOICEPRINTS)
    assert resolved == (
        room_track_identity.ResolvedTrackIdentity(
            raw_track_label="A",
            resolved_identity="Dylan",
            identity_method="pyannote-teammate",
            top_match_label="Dylan",
            top_match_confidence=0.93,
            second_match_label="Alex",
            second_match_confidence=0.61,
        ),
    )


def test_resolve_track_identities_marks_low_speech_track_unknown_even_with_strong_match() -> None:
    room_track_identity = _import_room_track_identity_module()
    bundle = RoomTrackBundle(raw_track_label="B", audio_bytes=b"track-b", speech_seconds=4.0)
    pyannote_client = FakePyannoteClient(
        {
            b"track-b": [
                IdentificationMatch(
                    speaker="speaker-b",
                    match="Dylan",
                    confidence={"Dylan": 0.97, "Alex": 0.12},
                )
            ]
        }
    )

    resolved = room_track_identity.resolve_track_identities(
        track_bundles=(bundle,),
        pyannote_client=pyannote_client,
        voiceprints=VOICEPRINTS,
    )

    assert len(pyannote_client.calls) == 1
    assert resolved == (
        room_track_identity.ResolvedTrackIdentity(
            raw_track_label="B",
            resolved_identity="unknown",
            identity_method="speech-too-short",
            top_match_label="Dylan",
            top_match_confidence=0.97,
            second_match_label="Alex",
            second_match_confidence=0.12,
        ),
    )


def test_resolve_track_identities_assigns_only_one_external_track_per_window() -> None:
    room_track_identity = _import_room_track_identity_module()
    bundles = (
        RoomTrackBundle(raw_track_label="C", audio_bytes=b"track-c", speech_seconds=10.0),
        RoomTrackBundle(raw_track_label="D", audio_bytes=b"track-d", speech_seconds=11.0),
    )
    pyannote_client = FakePyannoteClient(
        {
            b"track-c": [
                IdentificationMatch(
                    speaker="guest-c",
                    match=None,
                    confidence={"Dylan": 0.49, "Alex": 0.31, "Niyant": 0.18},
                )
            ],
            b"track-d": [
                IdentificationMatch(
                    speaker="guest-d",
                    match=None,
                    confidence={"Dylan": 0.42, "Alex": 0.28, "Niyant": 0.12},
                )
            ],
        }
    )

    resolved = room_track_identity.resolve_track_identities(
        track_bundles=bundles,
        pyannote_client=pyannote_client,
        voiceprints=VOICEPRINTS,
    )

    assert [item.raw_track_label for item in resolved] == ["C", "D"]
    assert [item.resolved_identity for item in resolved] == ["external-1", "unknown"]
    assert [item.identity_method for item in resolved] == ["pyannote-external", "external-slot-used"]
    assert resolved[0].top_match_label == "Dylan"
    assert resolved[0].top_match_confidence == 0.49
    assert resolved[0].second_match_label == "Alex"
    assert resolved[0].second_match_confidence == 0.31


def test_resolve_track_identities_leaves_ambiguous_teammate_candidate_unknown() -> None:
    room_track_identity = _import_room_track_identity_module()
    bundle = RoomTrackBundle(raw_track_label="A", audio_bytes=b"track-ambiguous", speech_seconds=14.0)
    pyannote_client = FakePyannoteClient(
        {
            b"track-ambiguous": [
                IdentificationMatch(
                    speaker="speaker-ambiguous",
                    match="Dylan",
                    confidence={"Dylan": 0.82, "Alex": 0.74, "Niyant": 0.05},
                )
            ]
        }
    )

    resolved = room_track_identity.resolve_track_identities(
        track_bundles=(bundle,),
        pyannote_client=pyannote_client,
        voiceprints=VOICEPRINTS,
    )

    assert resolved == (
        room_track_identity.ResolvedTrackIdentity(
            raw_track_label="A",
            resolved_identity="unknown",
            identity_method="pyannote-uncertain",
            top_match_label="Dylan",
            top_match_confidence=0.82,
            second_match_label="Alex",
            second_match_confidence=0.74,
        ),
    )
