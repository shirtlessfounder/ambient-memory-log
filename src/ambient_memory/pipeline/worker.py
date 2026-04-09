from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
import json
import logging
from pathlib import Path
from time import sleep
from typing import Any, Callable
import wave

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ambient_memory.config import DatabaseSettings, WorkerSettings, load_settings
from ambient_memory.db import build_session_factory
from ambient_memory.integrations.assemblyai_client import AssemblyAIClient, AssemblyAISpeakerProfile
from ambient_memory.integrations.deepgram_client import DeepgramClient
from ambient_memory.integrations.pyannote_client import IdentificationMatch, PyannoteClient, VoiceprintReference
from ambient_memory.logging import configure_logging
from ambient_memory.models import AudioChunk, Source, TranscriptCandidate, Voiceprint
from ambient_memory.pipeline.dedup import DedupCandidate, merge_transcript_candidates, persist_canonical_utterances
from ambient_memory.pipeline.normalize import normalize_deepgram_response
from ambient_memory.pipeline.room_windows import PendingRoomChunk as PendingRoomWindowChunk, select_room_windows
from ambient_memory.pipeline.speaker_matching import choose_speaker
from ambient_memory.pipeline.windows import WindowChunk, group_processing_windows


LOGGER = logging.getLogger(__name__)
UPLOADED_STATUS = "uploaded"
PROCESSED_STATUS = "processed"
FAILED_STATUS = "failed"
ROOM_ASSEMBLY_SOURCE_ID = "room-1"
ASSEMBLYAI_VENDOR = "assemblyai"


@dataclass(frozen=True, slots=True)
class WorkerRuntimeConfig:
    database_url: str
    database_ssl_root_cert: str | None = None
    aws_region: str | None = None
    deepgram_api_key: str | None = None
    pyannote_api_key: str | None = None
    assemblyai_api_key: str | None = None
    room_speaker_roster_path: str | None = None
    room_assembly_window_seconds: int = 600
    room_assembly_idle_flush_seconds: int = 120


@dataclass(frozen=True, slots=True)
class PendingChunk:
    id: str
    source_id: str
    source_type: str | None
    source_owner: str | None
    s3_bucket: str
    s3_key: str
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    pending_chunks: int
    windows: int
    processed_chunks: int
    failed_chunks: int
    dry_run: bool = False


class PipelineWorker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        s3_client: Any | None,
        deepgram_client: Any | None,
        pyannote_client: Any | None,
        assemblyai_client: Any | None = None,
        processing_version: str = "v1",
        room_speaker_roster_path: str | None = None,
        room_speakers: tuple[AssemblyAISpeakerProfile, ...] | None = None,
        room_assembly_window_seconds: int = 600,
        room_assembly_idle_flush_seconds: int = 120,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.s3_client = s3_client
        self.deepgram_client = deepgram_client
        self.pyannote_client = pyannote_client
        self.assemblyai_client = assemblyai_client
        self.processing_version = processing_version
        self.room_speaker_roster_path = room_speaker_roster_path
        self._room_speakers = room_speakers
        self.room_assembly_window_seconds = room_assembly_window_seconds
        self.room_assembly_idle_flush_seconds = room_assembly_idle_flush_seconds
        self.now = now or (lambda: datetime.now(UTC))

    def run_once(self, *, dry_run: bool = False) -> WorkerRunResult:
        pending_chunks = self._load_pending_chunks()
        room_chunks = {
            chunk_id: chunk
            for chunk_id, chunk in pending_chunks.items()
            if _uses_assemblyai(chunk.source_id)
        }
        legacy_chunks = {
            chunk_id: chunk
            for chunk_id, chunk in pending_chunks.items()
            if not _uses_assemblyai(chunk.source_id)
        }
        windows = self._group_windows(legacy_chunks)
        room_batches = self._select_room_batches(room_chunks)
        if dry_run:
            return WorkerRunResult(
                pending_chunks=len(pending_chunks),
                windows=len(windows) + len(room_batches),
                processed_chunks=0,
                failed_chunks=0,
                dry_run=True,
            )

        processed_chunks = 0
        failed_chunks = 0

        for window in windows:
            chunk_ids = [chunk.chunk_id for chunk in window.chunks]
            try:
                self._process_window(
                    chunks=[legacy_chunks[chunk_id] for chunk_id in chunk_ids],
                )
            except Exception as exc:
                LOGGER.exception("worker window failed chunk_ids=%s", chunk_ids)
                self._mark_chunks(chunk_ids, status=FAILED_STATUS, error_message=str(exc))
                failed_chunks += len(chunk_ids)
            else:
                processed_chunks += len(chunk_ids)

        for batch in room_batches:
            chunk_ids = [chunk.id for chunk in batch]
            try:
                processed = self._process_room_batch(chunks=batch)
            except Exception:
                LOGGER.exception("worker room batch failed chunk_ids=%s", chunk_ids)
                continue
            if processed:
                processed_chunks += len(chunk_ids)

        return WorkerRunResult(
            pending_chunks=len(pending_chunks),
            windows=len(windows) + len(room_batches),
            processed_chunks=processed_chunks,
            failed_chunks=failed_chunks,
        )

    def run(self, *, poll_seconds: float = 5.0) -> None:
        try:
            while True:
                self.run_once()
                sleep(poll_seconds)
        except KeyboardInterrupt:
            LOGGER.info("worker interrupted")

    def _group_windows(self, pending_chunks: dict[str, PendingChunk]):
        windows = group_processing_windows(
            WindowChunk(
                chunk_id=chunk.id,
                source_id=chunk.source_id,
                started_at=chunk.started_at,
                ended_at=chunk.ended_at,
            )
            for chunk in pending_chunks.values()
        )
        return sorted(
            windows,
            key=lambda window: (
                window.started_at,
                window.ended_at,
            ),
            reverse=True,
        )

    def _select_room_batches(self, pending_chunks: dict[str, PendingChunk]) -> list[tuple[PendingChunk, ...]]:
        selection = select_room_windows(
            (
                PendingRoomWindowChunk(
                    chunk_id=chunk.id,
                    source_id=chunk.source_id,
                    started_at=chunk.started_at,
                    ended_at=chunk.ended_at,
                )
                for chunk in pending_chunks.values()
            ),
            window_seconds=self.room_assembly_window_seconds,
            idle_flush_seconds=self.room_assembly_idle_flush_seconds,
            now=self.now(),
        )
        chunk_lookup = {chunk.id: chunk for chunk in pending_chunks.values()}
        return [
            tuple(chunk_lookup[batch_chunk.chunk_id] for batch_chunk in batch.chunks)
            for batch in selection.ready_batches
        ]

    def _load_pending_chunks(self) -> dict[str, PendingChunk]:
        session = self.session_factory()
        try:
            rows = session.execute(
                select(AudioChunk, Source)
                .join(Source, Source.id == AudioChunk.source_id, isouter=True)
                .where(AudioChunk.status == UPLOADED_STATUS)
                .order_by(AudioChunk.started_at, AudioChunk.ended_at, AudioChunk.source_id, AudioChunk.id)
            ).all()
        finally:
            session.close()

        return {
            chunk.id: PendingChunk(
                id=chunk.id,
                source_id=chunk.source_id,
                source_type=source.source_type if source is not None else None,
                source_owner=source.device_owner if source is not None else None,
                s3_bucket=chunk.s3_bucket,
                s3_key=chunk.s3_key,
                started_at=_normalize_timestamp(chunk.started_at),
                ended_at=_normalize_timestamp(chunk.ended_at),
            )
            for chunk, source in rows
        }

    def _process_window(self, *, chunks: list[PendingChunk]) -> None:
        if self.s3_client is None:
            raise RuntimeError("worker dependencies are not configured")

        session = self.session_factory()
        try:
            voiceprints: list[VoiceprintReference] | None = None
            dedup_candidates: list[DedupCandidate] = []

            for chunk in chunks:
                audio_bytes = self._load_audio_bytes(chunk)
                if voiceprints is None:
                    voiceprints = self._load_voiceprints(session)
                self._process_legacy_chunk(
                    session=session,
                    chunk=chunk,
                    audio_bytes=audio_bytes,
                    voiceprints=voiceprints,
                    dedup_candidates=dedup_candidates,
                )

            persist_canonical_utterances(
                session,
                merge_transcript_candidates(dedup_candidates),
                processing_version=self.processing_version,
            )
            self._mark_chunk_rows(
                session,
                chunk_ids=[chunk.id for chunk in chunks],
                status=PROCESSED_STATUS,
                error_message=None,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _process_room_batch(self, *, chunks: tuple[PendingChunk, ...]) -> bool:
        if self.assemblyai_client is None:
            raise RuntimeError("AssemblyAI client is not configured")

        room_speakers = self._load_room_speakers()
        audio_bytes = self._stitch_room_audio(chunks)
        utterances = self.assemblyai_client.transcribe_bytes(audio_bytes, speakers=room_speakers)
        if not any(utterance.speaker_name for utterance in utterances):
            return False

        session = self.session_factory()
        try:
            dedup_candidates: list[DedupCandidate] = []
            batch_started_at = chunks[0].started_at
            for utterance in utterances:
                started_at = batch_started_at + timedelta(seconds=utterance.start_seconds)
                ended_at = batch_started_at + timedelta(seconds=utterance.end_seconds)
                self._persist_transcript_candidate(
                    session=session,
                    chunk=self._room_chunk_for_utterance(
                        chunks,
                        started_at=started_at,
                        ended_at=ended_at,
                    ),
                    vendor=ASSEMBLYAI_VENDOR,
                    vendor_segment_id=utterance.vendor_segment_id,
                    text=utterance.text,
                    speaker_hint=utterance.speaker_hint,
                    speaker_name=utterance.speaker_name,
                    speaker_confidence=None,
                    confidence=utterance.confidence,
                    started_at=started_at,
                    ended_at=ended_at,
                    raw_payload=utterance.raw_payload,
                    dedup_candidates=dedup_candidates,
                )

            persist_canonical_utterances(
                session,
                merge_transcript_candidates(dedup_candidates),
                processing_version=self.processing_version,
            )
            self._mark_chunk_rows(
                session,
                chunk_ids=[chunk.id for chunk in chunks],
                status=PROCESSED_STATUS,
                error_message=None,
            )
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _process_legacy_chunk(
        self,
        *,
        session: Session,
        chunk: PendingChunk,
        audio_bytes: bytes,
        voiceprints: list[VoiceprintReference],
        dedup_candidates: list[DedupCandidate],
    ) -> None:
        if self.deepgram_client is None or self.pyannote_client is None:
            raise RuntimeError("legacy worker dependencies are not configured")

        deepgram_payload = self.deepgram_client.transcribe_bytes(audio_bytes, content_type="audio/wav")
        normalized_segments = normalize_deepgram_response(
            deepgram_payload,
            source_id=chunk.source_id,
            chunk_started_at=chunk.started_at,
        )
        identified_speakers = self._identify_speakers(
            audio_bytes=audio_bytes,
            filename=Path(chunk.s3_key).name,
            voiceprints=voiceprints,
        )

        for segment in normalized_segments:
            match = self._match_identification_for_segment(
                matches=identified_speakers,
                segment_started_at=segment.started_at,
                segment_ended_at=segment.ended_at,
                chunk_started_at=chunk.started_at,
            )
            speaker_name, speaker_confidence = self._resolve_speaker(
                source_type=chunk.source_type,
                source_owner=chunk.source_owner,
                match=match,
            )
            self._persist_transcript_candidate(
                session=session,
                chunk=chunk,
                vendor=segment.vendor,
                vendor_segment_id=segment.vendor_segment_id,
                text=segment.text,
                speaker_hint=segment.speaker_hint,
                speaker_name=speaker_name,
                speaker_confidence=speaker_confidence,
                confidence=segment.confidence,
                started_at=segment.started_at,
                ended_at=segment.ended_at,
                raw_payload=segment.raw_payload,
                dedup_candidates=dedup_candidates,
            )

    def _persist_transcript_candidate(
        self,
        *,
        session: Session,
        chunk: PendingChunk,
        vendor: str,
        vendor_segment_id: str | None,
        text: str,
        speaker_hint: str | None,
        speaker_name: str | None,
        speaker_confidence: float | None,
        confidence: float | None,
        started_at: datetime,
        ended_at: datetime,
        raw_payload: dict[str, Any],
        dedup_candidates: list[DedupCandidate],
    ) -> None:
        row = TranscriptCandidate(
            audio_chunk_id=chunk.id,
            source_id=chunk.source_id,
            vendor=vendor,
            vendor_segment_id=vendor_segment_id,
            text=text,
            speaker_hint=speaker_hint,
            speaker_confidence=speaker_confidence,
            confidence=confidence,
            started_at=started_at,
            ended_at=ended_at,
            raw_payload=raw_payload,
        )
        session.add(row)
        session.flush()
        dedup_candidates.append(
            DedupCandidate(
                transcript_candidate_id=row.id,
                source_id=row.source_id,
                source_owner=chunk.source_owner,
                text=row.text,
                started_at=row.started_at,
                ended_at=row.ended_at,
                speaker_name=speaker_name,
                speaker_confidence=speaker_confidence,
                confidence=row.confidence,
            )
        )

    def _identify_speakers(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        voiceprints: list[VoiceprintReference],
    ) -> list[IdentificationMatch]:
        if not voiceprints:
            return []

        return [
            match
            for match in self.pyannote_client.identify_speakers(
                audio_bytes=audio_bytes,
                filename=filename,
                voiceprints=voiceprints,
            )
            if match.speaker
        ]

    def _resolve_speaker(
        self,
        *,
        source_type: str | None,
        source_owner: str | None,
        match: IdentificationMatch | None,
    ) -> tuple[str | None, float | None]:
        if match is None:
            return None, None

        confidence = _match_confidence(match)
        resolved = choose_speaker(
            source_type=source_type,
            source_owner=source_owner,
            pyannote_match=match.match,
            confidence=confidence,
        )
        return resolved.speaker_name, resolved.confidence

    def _match_identification_for_segment(
        self,
        *,
        matches: list[IdentificationMatch],
        segment_started_at: datetime,
        segment_ended_at: datetime,
        chunk_started_at: datetime,
    ) -> IdentificationMatch | None:
        segment_start_seconds = max(0.0, (segment_started_at - chunk_started_at).total_seconds())
        segment_end_seconds = max(segment_start_seconds, (segment_ended_at - chunk_started_at).total_seconds())
        overlapping_matches = [
            match
            for match in matches
            if _overlap_seconds(
                segment_start=segment_start_seconds,
                segment_end=segment_end_seconds,
                match_start=match.start_seconds,
                match_end=match.end_seconds,
            )
            > 0
        ]
        if overlapping_matches:
            return max(
                overlapping_matches,
                key=lambda match: (
                    _overlap_seconds(
                        segment_start=segment_start_seconds,
                        segment_end=segment_end_seconds,
                        match_start=match.start_seconds,
                        match_end=match.end_seconds,
                    ),
                    _match_confidence(match) or 0.0,
                    -(match.start_seconds or 0.0),
                    match.speaker,
                    match.match or "",
                ),
            )

        untimed_matches = [
            match
            for match in matches
            if match.start_seconds is None or match.end_seconds is None
        ]
        if len(untimed_matches) == 1:
            return untimed_matches[0]

        return None

    def _load_audio_bytes(self, chunk: PendingChunk) -> bytes:
        response = self.s3_client.get_object(Bucket=chunk.s3_bucket, Key=chunk.s3_key)
        body = response["Body"]
        try:
            return body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

    def _stitch_room_audio(self, chunks: tuple[PendingChunk, ...]) -> bytes:
        output = BytesIO()
        wav_params: tuple[int, int, int, str, str] | None = None

        with wave.open(output, "wb") as output_wav:
            for chunk in chunks:
                chunk_audio = self._load_audio_bytes(chunk)
                with wave.open(BytesIO(chunk_audio), "rb") as input_wav:
                    current_params = (
                        input_wav.getnchannels(),
                        input_wav.getsampwidth(),
                        input_wav.getframerate(),
                        input_wav.getcomptype(),
                        input_wav.getcompname(),
                    )
                    if wav_params is None:
                        output_wav.setnchannels(current_params[0])
                        output_wav.setsampwidth(current_params[1])
                        output_wav.setframerate(current_params[2])
                        output_wav.setcomptype(current_params[3], current_params[4])
                        wav_params = current_params
                    elif current_params != wav_params:
                        raise RuntimeError("room batch audio chunks must share WAV parameters")

                    output_wav.writeframes(input_wav.readframes(input_wav.getnframes()))

        return output.getvalue()

    def _load_room_speakers(self) -> tuple[AssemblyAISpeakerProfile, ...]:
        if self._room_speakers is not None:
            return self._room_speakers
        if self.room_speaker_roster_path is None:
            raise RuntimeError("ROOM_SPEAKER_ROSTER_PATH is required for room batches")

        roster_path = Path(self.room_speaker_roster_path)
        payload = json.loads(roster_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError("room speaker roster must be a JSON array")

        speakers: list[AssemblyAISpeakerProfile] = []
        for item in payload:
            if not isinstance(item, dict):
                raise RuntimeError("room speaker roster entries must be JSON objects")

            name = _optional_roster_string(item.get("name"))
            if name is None:
                raise RuntimeError("room speaker roster entries must include a name")

            aliases_value = item.get("aliases", ())
            if aliases_value is None:
                aliases: tuple[str, ...] = ()
            elif isinstance(aliases_value, list | tuple):
                aliases = tuple(
                    alias
                    for alias in (_optional_roster_string(value) for value in aliases_value)
                    if alias is not None
                )
            else:
                raise RuntimeError("room speaker aliases must be a JSON array when present")

            speakers.append(
                AssemblyAISpeakerProfile(
                    name=name,
                    description=_optional_roster_string(item.get("description")),
                    aliases=aliases,
                )
            )

        if not speakers:
            raise RuntimeError("room speaker roster must include at least one speaker")

        self._room_speakers = tuple(speakers)
        return self._room_speakers

    def _room_chunk_for_utterance(
        self,
        chunks: tuple[PendingChunk, ...],
        *,
        started_at: datetime,
        ended_at: datetime,
    ) -> PendingChunk:
        best_chunk = chunks[0]
        best_overlap = -1.0

        for chunk in chunks:
            overlap = _datetime_overlap_seconds(
                started_at=started_at,
                ended_at=ended_at,
                chunk_started_at=chunk.started_at,
                chunk_ended_at=chunk.ended_at,
            )
            if overlap > best_overlap:
                best_chunk = chunk
                best_overlap = overlap

        if best_overlap > 0:
            return best_chunk

        for chunk in chunks:
            if started_at < chunk.ended_at:
                return chunk

        return chunks[-1]

    def _load_voiceprints(self, session: Session) -> list[VoiceprintReference]:
        rows = session.scalars(select(Voiceprint).order_by(Voiceprint.speaker_label, Voiceprint.id)).all()
        return [
            VoiceprintReference(
                label=row.speaker_label,
                voiceprint=row.provider_voiceprint_id,
            )
            for row in rows
        ]

    def _mark_chunks(self, chunk_ids: list[str], *, status: str, error_message: str | None) -> None:
        session = self.session_factory()
        try:
            self._mark_chunk_rows(session, chunk_ids=chunk_ids, status=status, error_message=error_message)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _mark_chunk_rows(
        self,
        session: Session,
        *,
        chunk_ids: list[str],
        status: str,
        error_message: str | None,
    ) -> None:
        rows = session.scalars(
            select(AudioChunk).where(AudioChunk.id.in_(chunk_ids))
        ).all()
        for row in rows:
            row.status = status
            row.error_message = error_message


def run_worker_once(*, dry_run: bool = False, env_file: str | None = None) -> WorkerRunResult:
    configure_logging()
    config = load_worker_runtime_config(dry_run=dry_run, env_file=env_file)
    session_factory = build_session_factory(
        DatabaseSettings(
            database_url=config.database_url,
            database_ssl_root_cert=config.database_ssl_root_cert,
        )
    )
    if dry_run:
        worker = PipelineWorker(
            session_factory=session_factory,
            s3_client=None,
            deepgram_client=None,
            pyannote_client=None,
            room_speaker_roster_path=config.room_speaker_roster_path,
            room_assembly_window_seconds=config.room_assembly_window_seconds,
            room_assembly_idle_flush_seconds=config.room_assembly_idle_flush_seconds,
        )
        return worker.run_once(dry_run=True)

    worker = build_worker(config)
    return worker.run_once()


def run_worker_loop(*, poll_seconds: float = 5.0, env_file: str | None = None) -> None:
    configure_logging()
    worker = build_worker(load_worker_runtime_config(dry_run=False, env_file=env_file))
    worker.run(poll_seconds=poll_seconds)


def build_worker(config: WorkerRuntimeConfig) -> PipelineWorker:
    if (
        config.aws_region is None
        or config.deepgram_api_key is None
        or config.pyannote_api_key is None
        or config.assemblyai_api_key is None
        or config.room_speaker_roster_path is None
    ):
        raise RuntimeError("worker runtime configuration is incomplete")

    return PipelineWorker(
        session_factory=build_session_factory(
            DatabaseSettings(
                database_url=config.database_url,
                database_ssl_root_cert=config.database_ssl_root_cert,
            )
        ),
        s3_client=build_s3_client(config.aws_region),
        deepgram_client=DeepgramClient(api_key=config.deepgram_api_key),
        pyannote_client=PyannoteClient(api_key=config.pyannote_api_key),
        assemblyai_client=AssemblyAIClient(api_key=config.assemblyai_api_key),
        room_speaker_roster_path=config.room_speaker_roster_path,
        room_assembly_window_seconds=config.room_assembly_window_seconds,
        room_assembly_idle_flush_seconds=config.room_assembly_idle_flush_seconds,
    )


def load_worker_runtime_config(*, dry_run: bool, env_file: str | None = None) -> WorkerRuntimeConfig:
    try:
        settings = load_settings(WorkerSettings, env_file=env_file)
    except ValidationError as exc:
        missing_fields = sorted(
            _worker_settings_field_name(str(error["loc"][0]))
            for error in exc.errors()
            if error.get("type") == "missing" and error.get("loc")
        )
        if missing_fields:
            raise RuntimeError(f"missing required environment variables: {', '.join(missing_fields)}") from exc
        invalid_fields = sorted(
            _worker_settings_field_name(str(error["loc"][0]))
            for error in exc.errors()
            if error.get("loc")
        )
        if invalid_fields:
            raise RuntimeError(f"invalid worker environment variables: {', '.join(invalid_fields)}") from exc
        raise

    if dry_run:
        return WorkerRuntimeConfig(
            database_url=settings.database_url,
            database_ssl_root_cert=settings.database_ssl_root_cert,
            room_speaker_roster_path=settings.room_speaker_roster_path,
            room_assembly_window_seconds=settings.room_assembly_window_seconds,
            room_assembly_idle_flush_seconds=settings.room_assembly_idle_flush_seconds,
        )

    required = {
        "AWS_REGION": settings.aws_region,
        "DEEPGRAM_API_KEY": settings.deepgram_api_key,
        "PYANNOTE_API_KEY": settings.pyannote_api_key,
        "ASSEMBLYAI_API_KEY": settings.assemblyai_api_key,
        "ROOM_SPEAKER_ROSTER_PATH": settings.room_speaker_roster_path,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"missing required environment variables: {', '.join(sorted(missing))}")

    return WorkerRuntimeConfig(
        database_url=settings.database_url,
        database_ssl_root_cert=settings.database_ssl_root_cert,
        aws_region=required["AWS_REGION"],
        deepgram_api_key=required["DEEPGRAM_API_KEY"],
        pyannote_api_key=required["PYANNOTE_API_KEY"],
        assemblyai_api_key=required["ASSEMBLYAI_API_KEY"],
        room_speaker_roster_path=required["ROOM_SPEAKER_ROSTER_PATH"],
        room_assembly_window_seconds=settings.room_assembly_window_seconds,
        room_assembly_idle_flush_seconds=settings.room_assembly_idle_flush_seconds,
    )


def build_s3_client(region_name: str) -> object:
    try:
        import boto3
    except ModuleNotFoundError as exc:
        raise RuntimeError("boto3 is required for worker execution") from exc

    return boto3.client("s3", region_name=region_name)


def _match_confidence(match: IdentificationMatch) -> float | None:
    if match.match and match.match in match.confidence:
        return match.confidence[match.match]
    if match.confidence:
        return max(match.confidence.values())
    return None


def _overlap_seconds(
    *,
    segment_start: float,
    segment_end: float,
    match_start: float | None,
    match_end: float | None,
) -> float:
    if match_start is None or match_end is None:
        return 0.0
    return max(0.0, min(segment_end, match_end) - max(segment_start, match_start))


def _uses_assemblyai(source_id: str) -> bool:
    return source_id == ROOM_ASSEMBLY_SOURCE_ID


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetime_overlap_seconds(
    *,
    started_at: datetime,
    ended_at: datetime,
    chunk_started_at: datetime,
    chunk_ended_at: datetime,
) -> float:
    return max(
        0.0,
        (
            min(ended_at, chunk_ended_at) -
            max(started_at, chunk_started_at)
        ).total_seconds(),
    )


def _optional_roster_string(value: Any) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


def _worker_settings_field_name(field_name: str) -> str:
    field = WorkerSettings.model_fields.get(field_name)
    if field is None or field.alias is None:
        return field_name
    return str(field.alias)
