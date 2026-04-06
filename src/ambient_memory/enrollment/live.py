from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import subprocess

from sqlalchemy.orm import Session

from ambient_memory.capture.agent import choose_audio_device, list_local_audio_devices
from ambient_memory.capture.device_discovery import AudioDevice
from ambient_memory.config import EnrollmentSettings
from ambient_memory.db import build_session_factory, normalize_speaker_label, upsert_voiceprint
from ambient_memory.integrations.pyannote_client import PyannoteClient


RECITATION_SCRIPT = [
    "Ambient Memory voiceprint enrollment. My name is Dylan and I am speaking in my normal working voice.",
    "Today I reviewed pull requests, checked logs, and updated our Postgres database after a migration.",
    "We talked about ambient memory, launchd jobs, Deepgram transcripts, pyannote matching, and Amazon S3 uploads.",
    "Tomorrow I may discuss product roadmaps, customer feedback, whiteboard notes, and API errors with the team.",
]


@dataclass(frozen=True, slots=True)
class LiveEnrollmentResult:
    speaker_label: str
    sample_path: Path
    replaced_existing: bool


def build_recitation_script(label: str) -> list[str]:
    personalized_first_line = (
        f"Ambient Memory voiceprint enrollment. My name is {label} and I am speaking in my normal working voice."
    )
    return [personalized_first_line, *RECITATION_SCRIPT[1:]]


def build_live_record_command(
    *,
    device: AudioDevice,
    output_path: Path | str,
    ffmpeg_binary: str = "ffmpeg",
) -> list[str]:
    return [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-f",
        "avfoundation",
        "-i",
        f":{device.index}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]


def run_live_voiceprint_enrollment(
    *,
    label: str,
    device_selection: str | None,
    ffmpeg_binary: str = "ffmpeg",
    settings: EnrollmentSettings | None = None,
    sample_dir: Path | str = Path("./voiceprints"),
    list_devices: Callable[..., list[AudioDevice]] = list_local_audio_devices,
    choose_device: Callable[[list[AudioDevice], str | None], AudioDevice] = choose_audio_device,
    start_recording: Callable[[list[str]], object] | None = None,
    prompt: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    now_factory: Callable[[], datetime] = datetime.now,
    client_factory: Callable[..., PyannoteClient] = PyannoteClient,
    session_factory: Callable[[], Session] | None = None,
    upsert_voiceprint: Callable[..., tuple[object, bool]] = upsert_voiceprint,
) -> LiveEnrollmentResult:
    runtime_settings = settings or EnrollmentSettings()
    canonical_label = label.strip()
    if not canonical_label:
        raise ValueError("label must not be empty")

    devices = list_devices(ffmpeg_binary=ffmpeg_binary)
    device = choose_device(devices, device_selection)
    normalized_label = normalize_speaker_label(canonical_label)
    target_dir = Path(sample_dir) / normalized_label
    target_dir.mkdir(parents=True, exist_ok=True)

    output(f"Recording voiceprint for {canonical_label}")
    output(f"Using device: {device.name}")
    output("Read this aloud in a quiet room:")
    for line in build_recitation_script(canonical_label):
        output(line)

    attempt = 0
    final_sample_path: Path | None = None
    while True:
        attempt += 1
        sample_path = target_dir / f"{now_factory().strftime('%Y%m%dT%H%M%S')}-attempt{attempt:02d}.wav"
        prompt("Press Enter to start recording: ")
        process = (start_recording or _start_recording)(
            build_live_record_command(
                device=device,
                output_path=sample_path,
                ffmpeg_binary=ffmpeg_binary,
            )
        )
        prompt("Recording. Press Enter to stop: ")
        _stop_recording(process)

        if not sample_path.exists() or sample_path.stat().st_size == 0:
            raise RuntimeError(f"voiceprint recording failed: {sample_path}")

        choice = prompt("Press Enter to enroll, type r to re-record, or q to cancel: ").strip().lower()
        if choice == "q":
            raise RuntimeError("voiceprint enrollment cancelled")
        if choice == "r":
            output("Re-recording voiceprint sample.")
            continue

        final_sample_path = sample_path
        break

    client = client_factory(api_key=runtime_settings.pyannote_api_key)
    voiceprint_id = client.enroll_voiceprint(
        label=canonical_label,
        audio_bytes=final_sample_path.read_bytes(),
        filename=final_sample_path.name,
    )

    factory = session_factory or build_session_factory(runtime_settings)
    session = factory()
    try:
        _, replaced_existing = upsert_voiceprint(
            session,
            speaker_label=canonical_label,
            provider_voiceprint_id=voiceprint_id,
            source_audio_key=str(final_sample_path),
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return LiveEnrollmentResult(
        speaker_label=canonical_label,
        sample_path=final_sample_path,
        replaced_existing=replaced_existing or attempt > 1,
    )


def _run_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _start_recording(command: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _stop_recording(process: object) -> None:
    terminate = getattr(process, "terminate")
    wait = getattr(process, "wait")
    terminate()
    try:
        wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill = getattr(process, "kill")
        kill()
        wait(timeout=10)
