from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Callable, Protocol

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.config import RoomEnrichmentSettings, load_settings
from ambient_memory.db import build_session_factory
from ambient_memory.integrations.pyannote_client import PyannoteError, VoiceprintReference
from ambient_memory.models import CanonicalUtterance, CanonicalUtteranceEnrichment, Voiceprint
from ambient_memory.pipeline.room_track_audio import build_room_window_audio, load_room_provenance_slices
from ambient_memory.pipeline.room_track_identity import (
    DEFAULT_MINIMUM_POOLED_SPEECH_SECONDS,
    DEFAULT_TEAMMATE_THRESHOLD,
    DEFAULT_TOP_VS_SECOND_MARGIN,
    resolve_track_identities,
)
from ambient_memory.pipeline.room_transcript_alignment import (
    CanonicalUtteranceWindowRow,
    align_retranscribed_segments,
)


DEFAULT_ROOM_ENRICHMENT_ENV_FILE = ".env.worker"
DEFAULT_ROOM_ENRICHMENT_RESOLVER_VENDOR = "room-v2"
DEFAULT_ROOM_ENRICHMENT_RESOLVER_VERSION = "room-v2-audio-identity-v1"
FIXED_WINDOW_MINUTES = 15
LOGGER = logging.getLogger(__name__)


class RoomEnrichmentError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RoomEnrichmentUtterance:
    canonical_utterance_id: str
    started_at: datetime
    ended_at: datetime
    raw_text: str
    current_speaker_label: str | None


@dataclass(frozen=True, slots=True)
class RoomEnrichmentSpeakerResolution:
    canonical_utterance_id: str
    resolved_speaker_name: str
    resolved_speaker_confidence: float | None
    resolution_notes: str | None


@dataclass(frozen=True, slots=True)
class RoomEnrichmentTextCleanup:
    canonical_utterance_id: str
    cleaned_text: str
    cleaned_text_confidence: float | None


@dataclass(frozen=True, slots=True)
class RoomEnrichmentWindow:
    started_at: datetime
    ended_at: datetime
    utterances: tuple[RoomEnrichmentUtterance, ...]


@dataclass(frozen=True, slots=True)
class RoomEnrichmentRunResult:
    source_id: str
    hours: int
    windows: int
    utterances: int
    created: int
    dry_run: bool


class RoomRetranscriptionClient(Protocol):
    vendor: str

    def transcribe_window(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        window_started_at: datetime,
        content_type: str = "audio/wav",
    ) -> list[object]: ...


def run_room_enrichment(
    *,
    hours: int,
    source_id: str,
    resolver_version: str,
    dry_run: bool = False,
    session_factory: sessionmaker[Session] | None = None,
    settings: RoomEnrichmentSettings | None = None,
    now: Callable[[], datetime] | None = None,
    s3_client: object | None = None,
    pyannote_client: object | None = None,
    retranscription_client: RoomRetranscriptionClient | None = None,
    voiceprints: Sequence[VoiceprintReference] | None = None,
) -> RoomEnrichmentRunResult:
    if hours <= 0:
        raise ValueError("hours must be positive")

    resolved_settings = settings
    resolved_session_factory = session_factory

    if resolved_session_factory is None:
        resolved_settings = _ensure_settings(resolved_settings)
        resolved_session_factory = build_session_factory(resolved_settings)

    resolved_now = now or (lambda: datetime.now(UTC))
    cutoff = resolved_now() - timedelta(hours=hours)
    resolver_vendor = DEFAULT_ROOM_ENRICHMENT_RESOLVER_VENDOR

    session = resolved_session_factory()
    try:
        rows = _load_pending_utterances(
            session,
            source_id=source_id,
            cutoff=cutoff,
            resolver_vendor=resolver_vendor,
            resolver_version=resolver_version,
        )
        windows = _group_fixed_windows(rows)
        if dry_run:
            return RoomEnrichmentRunResult(
                source_id=source_id,
                hours=hours,
                windows=len(windows),
                utterances=sum(len(window.utterances) for window in windows),
                created=0,
                dry_run=True,
            )

        if rows:
            resolved_voiceprints = (
                tuple(voiceprints)
                if voiceprints is not None
                else tuple(_load_voiceprints(session))
            )
            resolved_settings = _ensure_settings_for_runtime_dependencies(
                resolved_settings,
                s3_client=s3_client,
                pyannote_client=pyannote_client,
                retranscription_client=retranscription_client,
            )
            resolved_s3_client = _resolve_s3_client(s3_client, settings=resolved_settings)
            resolved_pyannote_client = _resolve_pyannote_client(
                pyannote_client,
                settings=resolved_settings,
            )
            resolved_retranscription_client = _resolve_retranscription_client(
                retranscription_client,
                settings=resolved_settings,
            )
        else:
            resolved_voiceprints = tuple(voiceprints or ())
            resolved_s3_client = s3_client
            resolved_pyannote_client = pyannote_client
            resolved_retranscription_client = retranscription_client

        created = 0
        for window in windows:
            created += _persist_window_enrichments(
                session,
                source_id=source_id,
                window=window,
                resolver_version=resolver_version,
                resolver_vendor=resolver_vendor,
                s3_client=resolved_s3_client,
                pyannote_client=resolved_pyannote_client,
                retranscription_client=resolved_retranscription_client,
                voiceprints=resolved_voiceprints,
                minimum_pooled_speech_seconds=(
                    resolved_settings.room_track_min_speech_seconds
                    if resolved_settings is not None
                    else DEFAULT_MINIMUM_POOLED_SPEECH_SECONDS
                ),
                teammate_threshold=(
                    resolved_settings.room_track_match_threshold
                    if resolved_settings is not None
                    else DEFAULT_TEAMMATE_THRESHOLD
                ),
                top_vs_second_margin=(
                    resolved_settings.room_track_match_margin
                    if resolved_settings is not None
                    else DEFAULT_TOP_VS_SECOND_MARGIN
                ),
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return RoomEnrichmentRunResult(
        source_id=source_id,
        hours=hours,
        windows=len(windows),
        utterances=sum(len(window.utterances) for window in windows),
        created=created,
        dry_run=False,
    )


def _load_pending_utterances(
    session: Session,
    *,
    source_id: str,
    cutoff: datetime,
    resolver_vendor: str,
    resolver_version: str,
) -> list[RoomEnrichmentUtterance]:
    stmt = (
        select(CanonicalUtterance)
        .outerjoin(
            CanonicalUtteranceEnrichment,
            and_(
                CanonicalUtteranceEnrichment.canonical_utterance_id == CanonicalUtterance.id,
                CanonicalUtteranceEnrichment.resolver_vendor == resolver_vendor,
                CanonicalUtteranceEnrichment.resolver_version == resolver_version,
            ),
        )
        .where(CanonicalUtterance.canonical_source_id == source_id)
        .where(CanonicalUtterance.started_at >= cutoff)
        .where(CanonicalUtteranceEnrichment.id.is_(None))
        .order_by(CanonicalUtterance.started_at, CanonicalUtterance.ended_at, CanonicalUtterance.id)
    )
    return [
        RoomEnrichmentUtterance(
            canonical_utterance_id=row.id,
            started_at=_normalize_timestamp(row.started_at),
            ended_at=_normalize_timestamp(row.ended_at),
            raw_text=row.text,
            current_speaker_label=row.speaker_name,
        )
        for row in session.scalars(stmt).all()
    ]


def _group_fixed_windows(utterances: list[RoomEnrichmentUtterance]) -> tuple[RoomEnrichmentWindow, ...]:
    if not utterances:
        return ()

    buckets: dict[datetime, list[RoomEnrichmentUtterance]] = {}
    for utterance in utterances:
        window_started_at = _floor_to_fixed_window(utterance.started_at)
        buckets.setdefault(window_started_at, []).append(utterance)

    ordered_window_starts = sorted(buckets)
    windows: list[RoomEnrichmentWindow] = []
    for window_started_at in ordered_window_starts:
        window_utterances = tuple(buckets[window_started_at])
        windows.append(
            RoomEnrichmentWindow(
                started_at=window_started_at,
                ended_at=window_started_at + timedelta(minutes=FIXED_WINDOW_MINUTES),
                utterances=window_utterances,
            )
        )
    return tuple(windows)


def _floor_to_fixed_window(value: datetime) -> datetime:
    return value.replace(
        minute=(value.minute // FIXED_WINDOW_MINUTES) * FIXED_WINDOW_MINUTES,
        second=0,
        microsecond=0,
    )


def _persist_window_enrichments(
    session: Session,
    *,
    source_id: str,
    window: RoomEnrichmentWindow,
    resolver_version: str,
    resolver_vendor: str,
    s3_client: object,
    pyannote_client: object,
    retranscription_client: RoomRetranscriptionClient,
    voiceprints: Sequence[VoiceprintReference],
    minimum_pooled_speech_seconds: float,
    teammate_threshold: float,
    top_vs_second_margin: float,
) -> int:
    canonical_by_id = _load_canonical_rows(
        session,
        utterance_ids=[utterance.canonical_utterance_id for utterance in window.utterances],
    )
    provenance_slices = tuple(
        load_room_provenance_slices(
            session,
            source_id=source_id,
            window_started_at=window.started_at,
            window_ended_at=window.ended_at,
        )
    )
    window_provenance_slices = _order_rows_by_canonical_utterance_id(
        window.utterances,
        provenance_slices,
        stage_name="room provenance",
        allow_extras=True,
    )

    window_audio = build_room_window_audio(provenance_slices, s3_client=s3_client)
    track_identities, identity_resolution_notes = _resolve_window_track_identities(
        track_bundles=window_audio.track_bundles,
        pyannote_client=pyannote_client,
        voiceprints=voiceprints,
        minimum_pooled_speech_seconds=minimum_pooled_speech_seconds,
        teammate_threshold=teammate_threshold,
        top_vs_second_margin=top_vs_second_margin,
    )
    track_identity_by_label = _index_track_identities(
        track_identities,
        expected_track_labels={item.raw_track_label for item in provenance_slices},
    )

    retranscribed_segments = retranscription_client.transcribe_window(
        audio_bytes=window_audio.audio_bytes,
        filename=_window_audio_filename(source_id=source_id, window_started_at=window.started_at),
        window_started_at=window.started_at,
    )
    aligned_rows = tuple(
        align_retranscribed_segments(
            _build_alignment_rows(window.utterances),
            retranscribed_segments,
            window_started_at=window.started_at,
        )
    )
    ordered_aligned_rows = _order_rows_by_canonical_utterance_id(
        window.utterances,
        aligned_rows,
        stage_name="transcript alignment",
    )

    for provenance_slice, aligned_row in zip(
        window_provenance_slices,
        ordered_aligned_rows,
        strict=True,
    ):
        canonical_row = canonical_by_id[provenance_slice.canonical_utterance_id]
        track_identity = track_identity_by_label[provenance_slice.raw_track_label]
        transcript_method, transcript_resolution_notes = _transcript_audit(
            aligned_row=aligned_row,
            retranscription_vendor=retranscription_client.vendor,
        )
        resolution_notes = _merge_resolution_notes(
            identity_resolution_notes.get(provenance_slice.raw_track_label),
            transcript_resolution_notes,
        )
        canonical_row.speaker_name = track_identity.resolved_identity
        canonical_row.speaker_confidence = track_identity.top_match_confidence
        session.add(
            CanonicalUtteranceEnrichment(
                canonical_utterance_id=provenance_slice.canonical_utterance_id,
                resolver_vendor=resolver_vendor,
                resolver_version=resolver_version,
                resolved_speaker_name=track_identity.resolved_identity,
                resolved_speaker_confidence=track_identity.top_match_confidence,
                cleaned_text=aligned_row.text,
                cleaned_text_confidence=aligned_row.confidence,
                identity_method=track_identity.identity_method,
                identity_track_label=provenance_slice.raw_track_label,
                identity_window_started_at=window.started_at,
                identity_match_label=track_identity.top_match_label,
                identity_match_confidence=track_identity.top_match_confidence,
                identity_second_match_label=track_identity.second_match_label,
                identity_second_match_confidence=track_identity.second_match_confidence,
                transcript_method=transcript_method,
                transcript_confidence=aligned_row.confidence,
                resolution_notes=resolution_notes,
            )
        )
    session.flush()
    return len(window.utterances)


def _load_canonical_rows(
    session: Session,
    *,
    utterance_ids: Sequence[str],
) -> dict[str, CanonicalUtterance]:
    rows = session.scalars(
        select(CanonicalUtterance).where(CanonicalUtterance.id.in_(tuple(utterance_ids)))
    ).all()
    rows_by_id = {
        row.id: row
        for row in rows
    }
    missing_ids = [utterance_id for utterance_id in utterance_ids if utterance_id not in rows_by_id]
    if missing_ids:
        raise RoomEnrichmentError(
            f"missing canonical utterance rows for room enrichment: {', '.join(sorted(missing_ids))}"
        )
    return rows_by_id


def _resolve_window_track_identities(
    *,
    track_bundles: Sequence[object],
    pyannote_client: object,
    voiceprints: Sequence[VoiceprintReference],
    minimum_pooled_speech_seconds: float,
    teammate_threshold: float,
    top_vs_second_margin: float,
) -> tuple[tuple[object, ...], dict[str, str | None]]:
    try:
        return (
            tuple(
                resolve_track_identities(
                    track_bundles=track_bundles,
                    pyannote_client=pyannote_client,
                    voiceprints=voiceprints,
                    minimum_pooled_speech_seconds=minimum_pooled_speech_seconds,
                    teammate_threshold=teammate_threshold,
                    top_vs_second_margin=top_vs_second_margin,
                )
            ),
            {},
        )
    except PyannoteError as exc:
        raw_track_labels = tuple(str(_required_attr(bundle, "raw_track_label")) for bundle in track_bundles)
        LOGGER.warning(
            "room identity fell back to raw track labels after pyannote failure track_labels=%s error=%s",
            list(raw_track_labels),
            exc,
        )
        return (
            tuple(_fallback_track_identity(raw_track_label) for raw_track_label in raw_track_labels),
            {
                raw_track_label: "speaker identity fell back to raw track label after pyannote failure"
                for raw_track_label in raw_track_labels
            },
        )


def _fallback_track_identity(raw_track_label: str) -> object:
    from ambient_memory.pipeline.room_track_identity import ResolvedTrackIdentity

    return ResolvedTrackIdentity(
        raw_track_label=raw_track_label,
        resolved_identity=raw_track_label,
        identity_method="raw-track-fallback",
        top_match_label=None,
        top_match_confidence=None,
        second_match_label=None,
        second_match_confidence=None,
    )


def _order_rows_by_canonical_utterance_id(
    source_utterances: tuple[RoomEnrichmentUtterance, ...],
    output_rows: Sequence[object],
    *,
    stage_name: str,
    allow_extras: bool = False,
) -> tuple[object, ...]:
    expected_ids = [utterance.canonical_utterance_id for utterance in source_utterances]
    available_by_id: dict[str, object] = {}
    for row in output_rows:
        canonical_utterance_id = str(_required_attr(row, "canonical_utterance_id"))
        if canonical_utterance_id in available_by_id:
            raise RoomEnrichmentError(
                f"{stage_name} must stay row-preserving; duplicate canonical_utterance_id {canonical_utterance_id}"
            )
        available_by_id[canonical_utterance_id] = row

    actual_ids = list(available_by_id)
    missing_ids = [
        canonical_utterance_id
        for canonical_utterance_id in expected_ids
        if canonical_utterance_id not in available_by_id
    ]
    extra_ids = [
        canonical_utterance_id
        for canonical_utterance_id in actual_ids
        if canonical_utterance_id not in expected_ids
    ]
    if missing_ids or (extra_ids and not allow_extras):
        raise RoomEnrichmentError(
            f"{stage_name} must stay row-preserving; expected {expected_ids}, got {actual_ids}"
        )
    return tuple(available_by_id[canonical_utterance_id] for canonical_utterance_id in expected_ids)


def _index_track_identities(
    track_identities: Sequence[object],
    *,
    expected_track_labels: set[str],
) -> dict[str, object]:
    indexed: dict[str, object] = {}
    for track_identity in track_identities:
        raw_track_label = str(_required_attr(track_identity, "raw_track_label"))
        if raw_track_label in indexed:
            raise RoomEnrichmentError(
                f"room track identity returned duplicate raw track label {raw_track_label}"
            )
        indexed[raw_track_label] = track_identity

    actual_track_labels = set(indexed)
    if actual_track_labels != expected_track_labels:
        raise RoomEnrichmentError(
            "room track identity must cover exactly the raw tracks in the window; "
            f"expected {sorted(expected_track_labels)}, got {sorted(actual_track_labels)}"
        )
    return indexed


def _build_alignment_rows(
    utterances: tuple[RoomEnrichmentUtterance, ...],
) -> tuple[CanonicalUtteranceWindowRow, ...]:
    return tuple(
        CanonicalUtteranceWindowRow(
            canonical_utterance_id=utterance.canonical_utterance_id,
            started_at=utterance.started_at,
            ended_at=utterance.ended_at,
            raw_text=utterance.raw_text,
        )
        for utterance in utterances
    )


def _transcript_audit(*, aligned_row: object, retranscription_vendor: str) -> tuple[str, str | None]:
    used_raw_fallback = bool(_required_attr(aligned_row, "used_raw_fallback"))
    if used_raw_fallback:
        return "raw-canonical-fallback", "transcript alignment fell back to raw canonical text"
    return f"{retranscription_vendor}-retranscribe", None


def _merge_resolution_notes(*notes: str | None) -> str | None:
    filtered = [note for note in notes if note]
    if not filtered:
        return None
    return "; ".join(filtered)


def _window_audio_filename(*, source_id: str, window_started_at: datetime) -> str:
    normalized_window_started_at = _normalize_timestamp(window_started_at).astimezone(UTC)
    return f"{source_id}-{normalized_window_started_at.strftime('%Y%m%dT%H%M%SZ')}.wav"


def _load_voiceprints(session: Session) -> tuple[VoiceprintReference, ...]:
    rows = session.scalars(select(Voiceprint).order_by(Voiceprint.speaker_label, Voiceprint.id)).all()
    return tuple(
        VoiceprintReference(
            label=row.speaker_label,
            voiceprint=row.provider_voiceprint_id,
        )
        for row in rows
    )


def _ensure_settings(settings: RoomEnrichmentSettings | None) -> RoomEnrichmentSettings:
    if settings is not None:
        return settings
    return load_settings(RoomEnrichmentSettings, env_file=DEFAULT_ROOM_ENRICHMENT_ENV_FILE)


def _ensure_settings_for_runtime_dependencies(
    settings: RoomEnrichmentSettings | None,
    *,
    s3_client: object | None,
    pyannote_client: object | None,
    retranscription_client: RoomRetranscriptionClient | None,
) -> RoomEnrichmentSettings | None:
    if s3_client is not None and pyannote_client is not None and retranscription_client is not None:
        return settings
    return _ensure_settings(settings)


def _resolve_s3_client(s3_client: object | None, *, settings: RoomEnrichmentSettings | None) -> object:
    if s3_client is not None:
        return s3_client

    if settings is None:
        raise RoomEnrichmentError("room enrichment settings are required to build the S3 client")

    from ambient_memory.pipeline.worker import build_s3_client

    return build_s3_client(settings.aws_region)


def _resolve_pyannote_client(
    pyannote_client: object | None,
    *,
    settings: RoomEnrichmentSettings | None,
) -> object:
    if pyannote_client is not None:
        return pyannote_client

    if settings is None:
        raise RoomEnrichmentError("room enrichment settings are required to build the pyannote client")

    from ambient_memory.integrations.pyannote_client import PyannoteClient

    return PyannoteClient(api_key=settings.pyannote_api_key)


def _resolve_retranscription_client(
    retranscription_client: RoomRetranscriptionClient | None,
    *,
    settings: RoomEnrichmentSettings | None,
) -> RoomRetranscriptionClient:
    if retranscription_client is not None:
        return retranscription_client

    if settings is None:
        raise RoomEnrichmentError("room enrichment settings are required to build the retranscription client")

    from ambient_memory.integrations.openai_room_retranscription_client import OpenAIRoomRetranscriptionClient

    return OpenAIRoomRetranscriptionClient(
        api_key=settings.openai_api_key,
        model=settings.openai_audio_transcribe_model,
    )


def _required_attr(row: object, name: str) -> object:
    if not hasattr(row, name):
        raise RoomEnrichmentError(f"room enrichment row is missing required attribute {name}")
    return getattr(row, name)


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
