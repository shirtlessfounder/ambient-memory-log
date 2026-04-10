from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
import math
from typing import Any, Protocol
import wave

from sqlalchemy import select
from sqlalchemy.orm import Session

from ambient_memory.models import AudioChunk, CanonicalUtterance, TranscriptCandidate, UtteranceSource
from ambient_memory.pipeline.room_speech import measure_speech_seconds


ROOM_TRACK_LABELS = ("A", "B", "C", "D")
_TRACK_ORDER = {label: index for index, label in enumerate(ROOM_TRACK_LABELS)}


@dataclass(frozen=True, slots=True)
class RoomProvenanceSlice:
    canonical_utterance_id: str
    transcript_candidate_id: str
    source_id: str
    raw_track_label: str
    utterance_started_at: datetime
    utterance_ended_at: datetime
    audio_chunk_id: str
    audio_chunk_started_at: datetime
    audio_chunk_ended_at: datetime
    s3_bucket: str
    s3_key: str


@dataclass(frozen=True, slots=True)
class RoomTrackBundle:
    raw_track_label: str
    audio_bytes: bytes
    speech_seconds: float
    utterance_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoomWindowAudio:
    audio_bytes: bytes
    track_bundles: tuple[RoomTrackBundle, ...]


class S3LikeClient(Protocol):
    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]: ...


def load_room_provenance_slices(
    session: Session,
    *,
    source_id: str,
    window_started_at: datetime,
    window_ended_at: datetime,
) -> tuple[RoomProvenanceSlice, ...]:
    normalized_window_started_at = _normalize_timestamp(window_started_at)
    normalized_window_ended_at = _normalize_timestamp(window_ended_at)

    stmt = (
        select(CanonicalUtterance, UtteranceSource, TranscriptCandidate, AudioChunk)
        .join(UtteranceSource, UtteranceSource.canonical_utterance_id == CanonicalUtterance.id)
        .join(TranscriptCandidate, TranscriptCandidate.id == UtteranceSource.transcript_candidate_id)
        .join(AudioChunk, AudioChunk.id == TranscriptCandidate.audio_chunk_id)
        .where(CanonicalUtterance.canonical_source_id == source_id)
        .where(CanonicalUtterance.started_at >= normalized_window_started_at)
        .where(CanonicalUtterance.started_at < normalized_window_ended_at)
        .where(TranscriptCandidate.source_id == source_id)
        .where(AudioChunk.source_id == source_id)
        .where(TranscriptCandidate.speaker_hint.in_(ROOM_TRACK_LABELS))
        .order_by(
            CanonicalUtterance.started_at,
            CanonicalUtterance.ended_at,
            CanonicalUtterance.id,
            UtteranceSource.is_canonical.desc(),
            TranscriptCandidate.started_at,
            TranscriptCandidate.ended_at,
            TranscriptCandidate.id,
        )
    )

    selected_by_utterance_id: dict[str, RoomProvenanceSlice] = {}
    for canonical_utterance, utterance_source, transcript_candidate, audio_chunk in session.execute(stmt):
        if canonical_utterance.id in selected_by_utterance_id:
            continue

        selected_by_utterance_id[canonical_utterance.id] = RoomProvenanceSlice(
            canonical_utterance_id=canonical_utterance.id,
            transcript_candidate_id=transcript_candidate.id,
            source_id=transcript_candidate.source_id,
            raw_track_label=str(transcript_candidate.speaker_hint),
            utterance_started_at=_normalize_timestamp(canonical_utterance.started_at),
            utterance_ended_at=_normalize_timestamp(canonical_utterance.ended_at),
            audio_chunk_id=audio_chunk.id,
            audio_chunk_started_at=_normalize_timestamp(audio_chunk.started_at),
            audio_chunk_ended_at=_normalize_timestamp(audio_chunk.ended_at),
            s3_bucket=audio_chunk.s3_bucket,
            s3_key=audio_chunk.s3_key,
        )

    return tuple(
        selected_by_utterance_id[utterance_id]
        for utterance_id in sorted(
            selected_by_utterance_id,
            key=lambda item: (
                selected_by_utterance_id[item].utterance_started_at,
                selected_by_utterance_id[item].utterance_ended_at,
                item,
            ),
        )
    )


def build_room_window_audio(
    provenance_slices: Sequence[RoomProvenanceSlice],
    *,
    s3_client: S3LikeClient,
    speech_seconds_measure: Callable[[bytes], float] | None = None,
) -> RoomWindowAudio:
    ordered_slices = tuple(sorted(provenance_slices, key=_slice_sort_key))
    if not ordered_slices:
        raise ValueError("room provenance slices are required to build room audio")
    speech_seconds_measure = speech_seconds_measure or measure_speech_seconds

    chunk_audio_cache: dict[tuple[str, str], bytes] = {}
    sliced_audio_by_utterance_id: dict[str, bytes] = {}
    for provenance_slice in ordered_slices:
        chunk_audio = _load_chunk_audio(
            s3_client,
            bucket=provenance_slice.s3_bucket,
            key=provenance_slice.s3_key,
            cache=chunk_audio_cache,
        )
        sliced_audio_by_utterance_id[provenance_slice.canonical_utterance_id] = _slice_chunk_audio_for_utterance(
            chunk_audio=chunk_audio,
            provenance_slice=provenance_slice,
        )

    window_audio_bytes = _stitch_wav_segments(
        [sliced_audio_by_utterance_id[item.canonical_utterance_id] for item in ordered_slices]
    )

    grouped_slices: dict[str, list[RoomProvenanceSlice]] = defaultdict(list)
    for provenance_slice in ordered_slices:
        grouped_slices[provenance_slice.raw_track_label].append(provenance_slice)

    track_bundles: list[RoomTrackBundle] = []
    for raw_track_label in sorted(grouped_slices, key=lambda label: (_TRACK_ORDER.get(label, math.inf), label)):
        track_slices = grouped_slices[raw_track_label]
        track_audio_bytes = _stitch_wav_segments(
            [sliced_audio_by_utterance_id[item.canonical_utterance_id] for item in track_slices]
        )
        track_bundles.append(
            RoomTrackBundle(
                raw_track_label=raw_track_label,
                audio_bytes=track_audio_bytes,
                speech_seconds=float(speech_seconds_measure(track_audio_bytes)),
                utterance_ids=tuple(item.canonical_utterance_id for item in track_slices),
            )
        )

    return RoomWindowAudio(audio_bytes=window_audio_bytes, track_bundles=tuple(track_bundles))


def _slice_sort_key(provenance_slice: RoomProvenanceSlice) -> tuple[datetime, datetime, str]:
    return (
        provenance_slice.utterance_started_at,
        provenance_slice.utterance_ended_at,
        provenance_slice.canonical_utterance_id,
    )


def _load_chunk_audio(
    s3_client: S3LikeClient,
    *,
    bucket: str,
    key: str,
    cache: dict[tuple[str, str], bytes],
) -> bytes:
    cache_key = (bucket, key)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    try:
        audio_bytes = body.read()
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()

    cache[cache_key] = audio_bytes
    return audio_bytes


def _slice_chunk_audio_for_utterance(
    *,
    chunk_audio: bytes,
    provenance_slice: RoomProvenanceSlice,
) -> bytes:
    start_seconds = max(
        0.0,
        (
            _normalize_timestamp(provenance_slice.utterance_started_at)
            - _normalize_timestamp(provenance_slice.audio_chunk_started_at)
        ).total_seconds(),
    )
    end_seconds = max(
        start_seconds,
        (
            _normalize_timestamp(provenance_slice.utterance_ended_at)
            - _normalize_timestamp(provenance_slice.audio_chunk_started_at)
        ).total_seconds(),
    )
    return _slice_wav_bytes(chunk_audio, start_seconds=start_seconds, end_seconds=end_seconds)


def _slice_wav_bytes(audio_bytes: bytes, *, start_seconds: float, end_seconds: float) -> bytes:
    with wave.open(BytesIO(audio_bytes), "rb") as input_wav:
        frame_rate = input_wav.getframerate()
        if frame_rate <= 0:
            raise RuntimeError("room chunk audio must declare a positive frame rate")

        total_frames = input_wav.getnframes()
        start_frame = min(total_frames, max(0, math.floor(start_seconds * frame_rate)))
        end_frame = min(total_frames, max(start_frame, math.ceil(end_seconds * frame_rate)))

        input_wav.setpos(start_frame)
        frames = input_wav.readframes(end_frame - start_frame)
        params = (
            input_wav.getnchannels(),
            input_wav.getsampwidth(),
            input_wav.getframerate(),
            input_wav.getcomptype(),
            input_wav.getcompname(),
        )

    output = BytesIO()
    with wave.open(output, "wb") as output_wav:
        output_wav.setnchannels(params[0])
        output_wav.setsampwidth(params[1])
        output_wav.setframerate(params[2])
        output_wav.setcomptype(params[3], params[4])
        output_wav.writeframes(frames)
    return output.getvalue()


def _stitch_wav_segments(audio_segments: Sequence[bytes]) -> bytes:
    if not audio_segments:
        raise ValueError("at least one WAV segment is required for stitching")

    output = BytesIO()
    wav_params: tuple[int, int, int, str, str] | None = None
    with wave.open(output, "wb") as output_wav:
        for segment_audio in audio_segments:
            with wave.open(BytesIO(segment_audio), "rb") as input_wav:
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
                    raise RuntimeError("room track audio segments must share WAV parameters")

                output_wav.writeframes(input_wav.readframes(input_wav.getnframes()))

    return output.getvalue()


def _normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
