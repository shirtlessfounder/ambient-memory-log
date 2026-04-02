from pathlib import Path

from ambient_memory.capture.device_discovery import AudioDevice
from ambient_memory.capture.ffmpeg import build_capture_command


def test_build_capture_command_uses_selected_audio_input():
    command = build_capture_command(
        device=AudioDevice(index="1", name="MacBook Pro Microphone"),
        spool_dir=Path("/tmp/ambient-spool"),
    )

    assert command[:6] == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "avfoundation",
    ]
    assert command[command.index("-i") + 1] == ":1"


def test_build_capture_command_sets_segment_duration():
    command = build_capture_command(
        device=AudioDevice(index="1", name="MacBook Pro Microphone"),
        spool_dir=Path("/tmp/ambient-spool"),
    )

    assert command[command.index("-segment_time") + 1] == "30"
    assert command[command.index("-strftime") + 1] == "1"
    assert command[-1] == "/tmp/ambient-spool/chunk-%Y%m%dT%H%M%S.wav"


def test_build_capture_command_uses_stable_wav_settings():
    command = build_capture_command(
        device=AudioDevice(index="1", name="MacBook Pro Microphone"),
        spool_dir=Path("/tmp/ambient-spool"),
    )

    assert "-ac" in command and command[command.index("-ac") + 1] == "1"
    assert "-ar" in command and command[command.index("-ar") + 1] == "16000"
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "pcm_s16le"


def test_build_capture_command_writes_chunks_into_spool_dir():
    spool_dir = Path("/tmp/ambient-spool")

    command = build_capture_command(
        device=AudioDevice(index="1", name="MacBook Pro Microphone"),
        spool_dir=spool_dir,
    )

    assert command[-1] == str(spool_dir / "chunk-%Y%m%dT%H%M%S.wav")
