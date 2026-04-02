from pathlib import Path
from uuid import uuid4

from ambient_memory.capture.device_discovery import AudioDevice


DEFAULT_SEGMENT_SECONDS = 30
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
OUTPUT_TEMPLATE = "chunk-{session_id}-%Y%m%dT%H%M%S.wav"


def build_capture_command(
    *,
    device: AudioDevice,
    spool_dir: Path | str,
    ffmpeg_binary: str = "ffmpeg",
    segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
    session_id: str | None = None,
) -> list[str]:
    spool_path = Path(spool_dir)
    output_path = spool_path / OUTPUT_TEMPLATE.format(
        session_id=session_id or uuid4().hex,
    )

    return [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-n",
        "-f",
        "avfoundation",
        "-i",
        f":{device.index}",
        "-vn",
        "-ac",
        str(DEFAULT_CHANNELS),
        "-ar",
        str(DEFAULT_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-f",
        "segment",
        "-segment_format",
        "wav",
        "-segment_time",
        str(segment_seconds),
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        str(output_path),
    ]
