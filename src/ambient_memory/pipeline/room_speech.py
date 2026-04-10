from __future__ import annotations

from io import BytesIO
import re
import subprocess
import wave


SILENCE_DURATION_PATTERN = re.compile(r"silence_duration:\s*(?P<value>\d+(?:\.\d+)?)", re.IGNORECASE)
DEFAULT_SILENCE_NOISE_DB = -25.0
DEFAULT_SILENCE_MIN_DURATION_SECONDS = 0.5


def measure_speech_seconds(
    audio_bytes: bytes,
    *,
    ffmpeg_binary: str = "ffmpeg",
    silence_noise_db: float = DEFAULT_SILENCE_NOISE_DB,
    silence_min_duration_seconds: float = DEFAULT_SILENCE_MIN_DURATION_SECONDS,
) -> float:
    total_duration = _wav_duration_seconds(audio_bytes)
    if total_duration <= 0:
        return 0.0

    result = subprocess.run(
        [
            ffmpeg_binary,
            "-hide_banner",
            "-nostdin",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-vn",
            "-sn",
            "-dn",
            "-af",
            f"silencedetect=noise={silence_noise_db}dB:d={silence_min_duration_seconds}",
            "-f",
            "null",
            "-",
        ],
        input=audio_bytes,
        capture_output=True,
        check=False,
    )
    output = b"\n".join(part for part in (result.stdout, result.stderr) if part).decode("utf-8", errors="replace")
    if result.returncode != 0:
        detail = output.strip() or f"return code {result.returncode}"
        raise RuntimeError(f"ffmpeg silence analysis failed: {detail}")

    silence_seconds = sum(float(match.group("value")) for match in SILENCE_DURATION_PATTERN.finditer(output))
    speech_seconds = total_duration - silence_seconds
    return max(0.0, speech_seconds)


def _wav_duration_seconds(audio_bytes: bytes) -> float:
    try:
        with wave.open(BytesIO(audio_bytes), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            if frame_rate <= 0:
                raise RuntimeError("wav audio must declare a positive frame rate")
            return wav_file.getnframes() / frame_rate
    except (wave.Error, EOFError) as exc:
        raise RuntimeError("room speech analysis requires valid WAV audio") from exc
