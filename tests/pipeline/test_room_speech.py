from __future__ import annotations

from io import BytesIO
import wave

import pytest

from ambient_memory.pipeline import room_speech


def _wav_bytes(frame_count: int, *, frame_rate: int = 16000) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        wav_file.writeframes(b"\x01\x00" * frame_count)
    return buffer.getvalue()


def test_measure_speech_seconds_subtracts_detected_silence(monkeypatch) -> None:
    audio_bytes = _wav_bytes(16000)

    def fake_run(*_args, **_kwargs):
        class Result:
            returncode = 0
            stdout = b""
            stderr = (
                b"[silencedetect @ 0x0] silence_end: 0.25 | silence_duration: 0.25\n"
                b"[silencedetect @ 0x0] silence_end: 0.50 | silence_duration: 0.25\n"
            )

        return Result()

    monkeypatch.setattr(room_speech.subprocess, "run", fake_run)

    speech_seconds = room_speech.measure_speech_seconds(audio_bytes)

    assert speech_seconds == pytest.approx(0.5)


def test_measure_speech_seconds_uses_room_tuned_default_silence_threshold(monkeypatch) -> None:
    audio_bytes = _wav_bytes(16000)
    command: list[str] = []

    def fake_run(args, **_kwargs):
        nonlocal command
        command = list(args)

        class Result:
            returncode = 0
            stdout = b""
            stderr = b""

        return Result()

    monkeypatch.setattr(room_speech.subprocess, "run", fake_run)

    room_speech.measure_speech_seconds(audio_bytes)

    assert "silencedetect=noise=-25.0dB:d=0.5" in command


def test_measure_speech_seconds_raises_when_ffmpeg_analysis_fails(monkeypatch) -> None:
    audio_bytes = _wav_bytes(16000)

    def fake_run(*_args, **_kwargs):
        class Result:
            returncode = 1
            stdout = b""
            stderr = b"ffmpeg exploded"

        return Result()

    monkeypatch.setattr(room_speech.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="ffmpeg silence analysis failed"):
        room_speech.measure_speech_seconds(audio_bytes)


def test_measure_speech_seconds_requires_valid_wav_audio() -> None:
    with pytest.raises(RuntimeError, match="valid WAV"):
        room_speech.measure_speech_seconds(b"not-a-wav")
