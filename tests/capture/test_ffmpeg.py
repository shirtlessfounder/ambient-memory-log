from pathlib import Path

from ambient_memory.capture.device_discovery import AudioDevice
from ambient_memory.capture.ffmpeg import build_capture_command


def test_build_capture_command_uses_selected_audio_input():
    command = build_capture_command(
        device=AudioDevice(index="1", name="MacBook Pro Microphone"),
        spool_dir=Path("/tmp/ambient-spool"),
    )

    assert command[:4] == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
    ]
    assert command[command.index("-f") + 1] == "avfoundation"
    assert command[command.index("-i") + 1] == ":1"


def test_build_capture_command_sets_segment_duration():
    command = build_capture_command(
        device=AudioDevice(index="1", name="MacBook Pro Microphone"),
        spool_dir=Path("/tmp/ambient-spool"),
        session_id="capture-session",
    )

    assert command[command.index("-segment_time") + 1] == "30"
    assert command[command.index("-strftime") + 1] == "1"
    assert command[-1] == "/tmp/ambient-spool/chunk-capture-session-%Y%m%dT%H%M%S.wav"


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
        session_id="capture-session",
    )

    assert command[-1] == str(spool_dir / "chunk-capture-session-%Y%m%dT%H%M%S.wav")


def test_build_capture_command_uses_non_interactive_output_flags():
    command = build_capture_command(
        device=AudioDevice(index="1", name="MacBook Pro Microphone"),
        spool_dir=Path("/tmp/ambient-spool"),
    )

    assert "-nostdin" in command
    assert "-n" in command


def test_build_capture_command_uses_unique_output_path_per_launch():
    first_command = build_capture_command(
        device=AudioDevice(index="1", name="MacBook Pro Microphone"),
        spool_dir=Path("/tmp/ambient-spool"),
    )
    second_command = build_capture_command(
        device=AudioDevice(index="1", name="MacBook Pro Microphone"),
        spool_dir=Path("/tmp/ambient-spool"),
    )

    assert first_command[-1] != second_command[-1]
