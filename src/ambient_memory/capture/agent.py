from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
import logging
import os
from pathlib import Path
import subprocess
from time import sleep
from uuid import uuid4

from ambient_memory.capture.device_discovery import AudioDevice, select_audio_device, parse_avfoundation_list
from ambient_memory.capture.ffmpeg import DEFAULT_SEGMENT_SECONDS, build_capture_command
from ambient_memory.capture.spool import LocalSpool
from ambient_memory.capture.uploader import ChunkUploader
from ambient_memory.db import build_session_factory, record_agent_heartbeat
from ambient_memory.logging import configure_logging


LOGGER = logging.getLogger(__name__)
DEFAULT_ACTIVE_START = "09:00"
DEFAULT_ACTIVE_END = "00:00"
DEFAULT_SOURCE_ID = "desk-a"
DEFAULT_SOURCE_TYPE = "macbook"
DEFAULT_SPOOL_DIR = "./spool"


@dataclass(frozen=True, slots=True)
class AgentRuntimeConfig:
    source_id: str
    source_type: str
    device_owner: str | None
    spool_dir: Path
    active_start_local: str
    active_end_local: str
    aws_region: str | None = None
    s3_bucket: str | None = None
    database_url: str | None = None
    database_ssl_root_cert: str | None = None


@dataclass(frozen=True, slots=True)
class DatabaseRuntimeSettings:
    database_url: str
    database_ssl_root_cert: str | None = None


class CaptureAgent:
    def __init__(
        self,
        *,
        config: AgentRuntimeConfig,
        device: AudioDevice,
        uploader: ChunkUploader,
        ffmpeg_binary: str = "ffmpeg",
        poll_seconds: int = 2,
        heartbeat_seconds: int = 30,
    ) -> None:
        self.config = config
        self.device = device
        self.uploader = uploader
        self.ffmpeg_binary = ffmpeg_binary
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.spool = uploader.spool
        self._process: subprocess.Popen[bytes] | None = None
        self._last_heartbeat_at: datetime | None = None

    def run(self) -> None:
        active_start = parse_local_time(self.config.active_start_local)
        active_end = parse_local_time(self.config.active_end_local)
        self.spool.ensure()

        try:
            while True:
                now = datetime.now().astimezone()
                if is_within_active_window(now=now.time(), start=active_start, end=active_end):
                    self._ensure_capture_running()
                    upload_result = self.uploader.upload_ready()
                    self._maybe_heartbeat(uploaded=upload_result.uploaded > 0)
                else:
                    self._stop_capture()
                    self.uploader.upload_ready()
                    self._maybe_heartbeat(uploaded=False)

                sleep(self.poll_seconds)
        except KeyboardInterrupt:
            LOGGER.info("capture agent interrupted")
        finally:
            self._stop_capture()

    def _ensure_capture_running(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        if self._process is not None and self._process.poll() is not None:
            LOGGER.warning("ffmpeg exited with return code %s; restarting", self._process.returncode)

        self._process = subprocess.Popen(
            build_capture_command(
                device=self.device,
                spool_dir=self.config.spool_dir,
                ffmpeg_binary=self.ffmpeg_binary,
                segment_seconds=DEFAULT_SEGMENT_SECONDS,
                session_id=f"{self.config.source_id}-{uuid4().hex[:8]}",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _stop_capture(self) -> None:
        if self._process is None:
            return

        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=10)

        self._process = None

    def _maybe_heartbeat(self, *, uploaded: bool) -> None:
        now = datetime.now(UTC)
        if not uploaded and self._last_heartbeat_at is not None:
            elapsed = (now - self._last_heartbeat_at).total_seconds()
            if elapsed < self.heartbeat_seconds:
                return

        session = self.uploader.session_factory()
        try:
            record_agent_heartbeat(
                session,
                source_id=self.config.source_id,
                source_type=self.config.source_type,
                device_owner=self.config.device_owner,
                seen_at=now,
                uploaded_at=now if uploaded else None,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        self._last_heartbeat_at = now


def list_local_audio_devices(ffmpeg_binary: str = "ffmpeg") -> list[AudioDevice]:
    result = subprocess.run(
        [
            ffmpeg_binary,
            "-hide_banner",
            "-f",
            "avfoundation",
            "-list_devices",
            "true",
            "-i",
            "",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    devices = parse_avfoundation_list(output)
    if devices:
        return devices

    raise RuntimeError(output.strip() or f"{ffmpeg_binary} did not report any audio devices")


def choose_audio_device(devices: list[AudioDevice], selection: str | None) -> AudioDevice:
    if selection is not None:
        return select_audio_device(devices, selection)

    ranked_devices = sorted(devices, key=_device_preference_score, reverse=True)
    if ranked_devices and _device_preference_score(ranked_devices[0]) > 0:
        return ranked_devices[0]

    return select_audio_device(devices, selection)


def run_capture_agent(
    *,
    dry_run: bool,
    ffmpeg_binary: str = "ffmpeg",
    device_selection: str | None = None,
) -> None:
    configure_logging()
    config = load_runtime_config(dry_run=dry_run)
    spool = LocalSpool(config.spool_dir)
    spool.ensure()
    devices = list_local_audio_devices(ffmpeg_binary)
    device = choose_audio_device(devices, device_selection)

    if dry_run:
        LOGGER.info("dry-run device=%s", device.name)
        LOGGER.info("dry-run spool_dir=%s", config.spool_dir.resolve())
        LOGGER.info(
            "dry-run active_window=%s-%s",
            config.active_start_local,
            config.active_end_local,
        )
        print(f"device: {device.index}: {device.name}")
        print(f"spool: {config.spool_dir.resolve()}")
        print(f"active-window: {config.active_start_local} -> {config.active_end_local}")
        return

    if config.aws_region is None or config.s3_bucket is None or config.database_url is None:
        raise RuntimeError("missing runtime configuration for uploads and database access")

    s3_client = build_s3_client(config.aws_region)
    session_factory = build_session_factory(
        DatabaseRuntimeSettings(
            database_url=config.database_url,
            database_ssl_root_cert=config.database_ssl_root_cert,
        )
    )
    uploader = ChunkUploader(
        spool=spool,
        s3_client=s3_client,
        session_factory=session_factory,
        bucket=config.s3_bucket,
        source_id=config.source_id,
        source_type=config.source_type,
        device_owner=config.device_owner,
        segment_seconds=DEFAULT_SEGMENT_SECONDS,
    )
    agent = CaptureAgent(
        config=config,
        device=device,
        uploader=uploader,
        ffmpeg_binary=ffmpeg_binary,
    )
    agent.run()


def parse_local_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def is_within_active_window(*, now: time, start: time, end: time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def load_runtime_config(*, dry_run: bool) -> AgentRuntimeConfig:
    source_id = os.getenv("SOURCE_ID", DEFAULT_SOURCE_ID)
    source_type = os.getenv("SOURCE_TYPE", DEFAULT_SOURCE_TYPE)
    spool_dir = Path(os.getenv("SPOOL_DIR", DEFAULT_SPOOL_DIR))
    active_start_local = os.getenv("ACTIVE_START_LOCAL", DEFAULT_ACTIVE_START)
    active_end_local = os.getenv("ACTIVE_END_LOCAL", DEFAULT_ACTIVE_END)
    device_owner = os.getenv("DEVICE_OWNER")

    if dry_run:
        return AgentRuntimeConfig(
            source_id=source_id,
            source_type=source_type,
            device_owner=device_owner,
            spool_dir=spool_dir,
            active_start_local=active_start_local,
            active_end_local=active_end_local,
        )

    required = {
        "AWS_REGION": os.getenv("AWS_REGION"),
        "S3_BUCKET": os.getenv("S3_BUCKET"),
        "DATABASE_URL": os.getenv("DATABASE_URL"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"missing required environment variables: {', '.join(sorted(missing))}")

    return AgentRuntimeConfig(
        source_id=source_id,
        source_type=source_type,
        device_owner=device_owner,
        spool_dir=spool_dir,
        active_start_local=active_start_local,
        active_end_local=active_end_local,
        aws_region=required["AWS_REGION"],
        s3_bucket=required["S3_BUCKET"],
        database_url=required["DATABASE_URL"],
        database_ssl_root_cert=os.getenv("DATABASE_SSL_ROOT_CERT"),
    )


def build_s3_client(region_name: str) -> object:
    try:
        import boto3
    except ModuleNotFoundError as exc:
        raise RuntimeError("boto3 is required for non-dry-run uploads") from exc

    return boto3.client("s3", region_name=region_name)


def _device_preference_score(device: AudioDevice) -> int:
    name = device.name.lower()
    score = 0

    if "built-in" in name:
        score += 50
    if "macbook" in name:
        score += 40
    if "microphone" in name:
        score += 20
    if "iphone" in name or "ipad" in name:
        score -= 40
    if "external" in name or "usb" in name:
        score -= 20

    return score
