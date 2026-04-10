from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.config import RoomEnrichmentSettings, load_settings
from ambient_memory.db import build_session_factory
from ambient_memory.models import CanonicalUtterance, CanonicalUtteranceEnrichment


DEFAULT_ROOM_ENRICHMENT_ENV_FILE = ".env.worker"
DEFAULT_ROOM_ENRICHMENT_RESOLVER_VERSION = "openai-room-v1"
FIXED_WINDOW_MINUTES = 15
ALLOWED_RESOLVED_SPEAKERS = ("Dylan", "Niyant", "Alex", "Jakub", "unknown")


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


class RoomEnrichmentResolver(Protocol):
    vendor: str

    def resolve_speakers(
        self,
        utterances: tuple[RoomEnrichmentUtterance, ...],
        *,
        allowed_speakers: tuple[str, ...],
    ) -> list[RoomEnrichmentSpeakerResolution]: ...

    def cleanup_text(
        self,
        utterances: tuple[RoomEnrichmentUtterance, ...],
        *,
        speaker_resolutions: tuple[RoomEnrichmentSpeakerResolution, ...],
    ) -> list[RoomEnrichmentTextCleanup]: ...


def run_room_enrichment(
    *,
    hours: int,
    source_id: str,
    resolver_version: str,
    dry_run: bool = False,
    session_factory: sessionmaker[Session] | None = None,
    resolver: RoomEnrichmentResolver | None = None,
    settings: RoomEnrichmentSettings | None = None,
    now: Callable[[], datetime] | None = None,
) -> RoomEnrichmentRunResult:
    if hours <= 0:
        raise ValueError("hours must be positive")

    resolved_settings = settings
    if resolved_settings is None and (session_factory is None or resolver is None):
        resolved_settings = load_settings(RoomEnrichmentSettings, env_file=DEFAULT_ROOM_ENRICHMENT_ENV_FILE)

    resolved_session_factory = session_factory
    if resolved_session_factory is None:
        assert resolved_settings is not None
        resolved_session_factory = build_session_factory(resolved_settings)

    resolved_resolver = resolver
    if resolved_resolver is None:
        assert resolved_settings is not None
        from ambient_memory.integrations.openai_room_enrichment_client import OpenAIRoomEnrichmentClient

        resolved_resolver = OpenAIRoomEnrichmentClient(api_key=resolved_settings.openai_api_key)

    resolved_now = now or (lambda: datetime.now(UTC))
    cutoff = resolved_now() - timedelta(hours=hours)
    resolver_vendor = resolved_resolver.vendor

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

        created = 0
        for window in windows:
            created += _persist_window_enrichments(
                session,
                window=window,
                resolver=resolved_resolver,
                resolver_version=resolver_version,
                resolver_vendor=resolver_vendor,
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
    window: RoomEnrichmentWindow,
    resolver: RoomEnrichmentResolver,
    resolver_version: str,
    resolver_vendor: str,
) -> int:
    speaker_resolutions = tuple(
        resolver.resolve_speakers(
            window.utterances,
            allowed_speakers=ALLOWED_RESOLVED_SPEAKERS,
        )
    )
    _validate_row_preserving(
        window.utterances,
        speaker_resolutions,
        stage_name="speaker resolution",
    )
    text_cleanups = tuple(
        resolver.cleanup_text(
            window.utterances,
            speaker_resolutions=speaker_resolutions,
        )
    )
    _validate_row_preserving(
        window.utterances,
        text_cleanups,
        stage_name="text cleanup",
    )

    cleanup_by_id = {
        cleanup.canonical_utterance_id: cleanup
        for cleanup in text_cleanups
    }
    for resolution in speaker_resolutions:
        cleanup = cleanup_by_id[resolution.canonical_utterance_id]
        session.add(
            CanonicalUtteranceEnrichment(
                canonical_utterance_id=resolution.canonical_utterance_id,
                resolver_vendor=resolver_vendor,
                resolver_version=resolver_version,
                resolved_speaker_name=resolution.resolved_speaker_name,
                resolved_speaker_confidence=resolution.resolved_speaker_confidence,
                cleaned_text=cleanup.cleaned_text,
                cleaned_text_confidence=cleanup.cleaned_text_confidence,
                resolution_notes=_normalize_optional_text(resolution.resolution_notes),
            )
        )
    session.flush()
    return len(window.utterances)


def _validate_row_preserving(
    source_utterances: tuple[RoomEnrichmentUtterance, ...],
    output_rows: tuple[RoomEnrichmentSpeakerResolution, ...] | tuple[RoomEnrichmentTextCleanup, ...],
    *,
    stage_name: str,
) -> None:
    expected_ids = [utterance.canonical_utterance_id for utterance in source_utterances]
    actual_ids = [row.canonical_utterance_id for row in output_rows]
    if actual_ids != expected_ids:
        raise RoomEnrichmentError(
            f"{stage_name} must stay row-preserving; expected {expected_ids}, got {actual_ids}"
        )


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)
