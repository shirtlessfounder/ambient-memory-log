from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ambient_memory.integrations.pyannote_client import IdentificationMatch, VoiceprintReference


DEFAULT_MINIMUM_POOLED_SPEECH_SECONDS = 8.0
DEFAULT_TEAMMATE_THRESHOLD = 0.75
DEFAULT_TOP_VS_SECOND_MARGIN = 0.15


@dataclass(frozen=True, slots=True)
class ResolvedTrackIdentity:
    raw_track_label: str
    resolved_identity: str
    identity_method: str
    top_match_label: str | None
    top_match_confidence: float | None
    second_match_label: str | None
    second_match_confidence: float | None


@dataclass(frozen=True, slots=True)
class _MatchAudit:
    top_match_label: str | None
    top_match_confidence: float | None
    second_match_label: str | None
    second_match_confidence: float | None


class PyannoteTrackIdentityClient(Protocol):
    def identify_speakers(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        voiceprints: list[VoiceprintReference],
        matching_threshold: int = 0,
        exclusive: bool = True,
    ) -> list[IdentificationMatch]: ...


def resolve_track_identities(
    *,
    track_bundles: Sequence[object],
    pyannote_client: PyannoteTrackIdentityClient,
    voiceprints: Sequence[VoiceprintReference],
    minimum_pooled_speech_seconds: float = DEFAULT_MINIMUM_POOLED_SPEECH_SECONDS,
    teammate_threshold: float = DEFAULT_TEAMMATE_THRESHOLD,
    top_vs_second_margin: float = DEFAULT_TOP_VS_SECOND_MARGIN,
) -> tuple[ResolvedTrackIdentity, ...]:
    voiceprint_list = list(voiceprints)
    teammate_labels = {voiceprint.label for voiceprint in voiceprint_list}
    external_slot_used = False
    resolved: list[ResolvedTrackIdentity] = []

    for bundle in track_bundles:
        raw_track_label = _bundle_raw_track_label(bundle)
        speech_seconds = _bundle_speech_seconds(bundle)
        matches = _identify_track_bundle(
            pyannote_client=pyannote_client,
            bundle=bundle,
            raw_track_label=raw_track_label,
            voiceprints=voiceprint_list,
        )
        audit = _build_match_audit(matches, teammate_labels=teammate_labels)

        if speech_seconds < minimum_pooled_speech_seconds:
            resolved.append(_resolved_identity(raw_track_label=raw_track_label, resolved_identity="unknown", identity_method="speech-too-short", audit=audit))
            continue

        if _is_confident_teammate_match(
            audit=audit,
            teammate_threshold=teammate_threshold,
            top_vs_second_margin=top_vs_second_margin,
        ):
            resolved.append(
                _resolved_identity(
                    raw_track_label=raw_track_label,
                    resolved_identity=audit.top_match_label or "unknown",
                    identity_method="pyannote-teammate",
                    audit=audit,
                )
            )
            continue

        if _is_coherent_non_teammate_track(
            matches,
            audit=audit,
            teammate_threshold=teammate_threshold,
        ):
            if not external_slot_used:
                external_slot_used = True
                resolved.append(
                    _resolved_identity(
                        raw_track_label=raw_track_label,
                        resolved_identity="external-1",
                        identity_method="pyannote-external",
                        audit=audit,
                    )
                )
            else:
                resolved.append(
                    _resolved_identity(
                        raw_track_label=raw_track_label,
                        resolved_identity="unknown",
                        identity_method="external-slot-used",
                        audit=audit,
                    )
                )
            continue

        identity_method = "no-voiceprints" if not voiceprint_list else "pyannote-uncertain"
        resolved.append(
            _resolved_identity(
                raw_track_label=raw_track_label,
                resolved_identity="unknown",
                identity_method=identity_method,
                audit=audit,
            )
        )

    return tuple(resolved)


def _identify_track_bundle(
    *,
    pyannote_client: PyannoteTrackIdentityClient,
    bundle: object,
    raw_track_label: str,
    voiceprints: list[VoiceprintReference],
) -> list[IdentificationMatch]:
    if not voiceprints:
        return []

    return pyannote_client.identify_speakers(
        audio_bytes=_bundle_audio_bytes(bundle),
        filename=f"room-track-{raw_track_label}.wav",
        voiceprints=voiceprints,
    )


def _build_match_audit(
    matches: Sequence[IdentificationMatch],
    *,
    teammate_labels: set[str],
) -> _MatchAudit:
    scores: dict[str, float] = {}
    for match in matches:
        for label, score in match.confidence.items():
            if label not in teammate_labels:
                continue
            normalized = float(score)
            previous = scores.get(label)
            if previous is None or normalized > previous:
                scores[label] = normalized

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    top = ranked[0] if ranked else (None, None)
    second = ranked[1] if len(ranked) > 1 else (None, None)
    return _MatchAudit(
        top_match_label=top[0],
        top_match_confidence=top[1],
        second_match_label=second[0],
        second_match_confidence=second[1],
    )


def _is_confident_teammate_match(
    *,
    audit: _MatchAudit,
    teammate_threshold: float,
    top_vs_second_margin: float,
) -> bool:
    if audit.top_match_label is None or audit.top_match_confidence is None:
        return False

    if audit.top_match_confidence < teammate_threshold:
        return False

    second_confidence = audit.second_match_confidence or 0.0
    return (audit.top_match_confidence - second_confidence) >= top_vs_second_margin


def _is_coherent_non_teammate_track(
    matches: Sequence[IdentificationMatch],
    *,
    audit: _MatchAudit,
    teammate_threshold: float,
) -> bool:
    if not matches:
        return False

    if audit.top_match_confidence is not None and audit.top_match_confidence >= teammate_threshold:
        return False

    speakers = {
        match.speaker.strip()
        for match in matches
        if match.speaker and match.speaker.strip()
    }
    return len(speakers) == 1


def _resolved_identity(
    *,
    raw_track_label: str,
    resolved_identity: str,
    identity_method: str,
    audit: _MatchAudit,
) -> ResolvedTrackIdentity:
    return ResolvedTrackIdentity(
        raw_track_label=raw_track_label,
        resolved_identity=resolved_identity,
        identity_method=identity_method,
        top_match_label=audit.top_match_label,
        top_match_confidence=audit.top_match_confidence,
        second_match_label=audit.second_match_label,
        second_match_confidence=audit.second_match_confidence,
    )


def _bundle_raw_track_label(bundle: object) -> str:
    return str(_bundle_attr(bundle, "raw_track_label", "track_label"))


def _bundle_audio_bytes(bundle: object) -> bytes:
    value = _bundle_attr(bundle, "audio_bytes", "stitched_audio_bytes")
    if not isinstance(value, bytes):
        raise TypeError("track bundle audio must be bytes")
    return value


def _bundle_speech_seconds(bundle: object) -> float:
    value = _bundle_attr(bundle, "speech_seconds", "pooled_speech_seconds")
    return float(value)


def _bundle_attr(bundle: object, *names: str) -> Any:
    for name in names:
        if hasattr(bundle, name):
            return getattr(bundle, name)
    joined = ", ".join(names)
    raise AttributeError(f"track bundle is missing one of the required attributes: {joined}")
