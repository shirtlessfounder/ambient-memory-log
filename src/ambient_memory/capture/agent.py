from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
import logging
from pathlib import Path
import signal
import subprocess
from time import sleep
from typing import Callable
from uuid import uuid4

from ambient_memory.capture.device_discovery import AudioDevice, select_audio_device, parse_avfoundation_list
from ambient_memory.capture.ffmpeg import DEFAULT_SEGMENT_SECONDS, build_capture_command
from ambient_memory.capture.spool import LocalSpool, SpoolBacklogFullError
from ambient_memory.capture.uploader import ChunkUploader, UploadBatchResult
from ambient_memory.config import CaptureSettings, DatabaseSettings, load_settings
from ambient_memory.db import build_session_factory, record_agent_heartbeat
from ambient_memory.logging import configure_logging


LOGGER = logging.getLogger(__name__)
CAPTURE_STALL_TIMEOUT_SECONDS = (DEFAULT_SEGMENT_SECONDS * 2) + 5


class CaptureAgentShutdown(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(f"capture agent received signal {signum}")


@dataclass(frozen=True, slots=True)
class AgentRuntimeConfig:
    source_id: str
    source_type: str
    device_owner: str | None
    spool_dir: Path
    capture_device_name: str | None
    max_backlog_files: int
    active_start_local: str
    active_end_local: str
    silence_filter_enabled: bool = True
    silence_max_volume_db: float = -45.0
    aws_region: str | None = None
    s3_bucket: str | None = None
    database_url: str | None = None
    database_ssl_root_cert: str | None = None


class CaptureAgent:
    def __init__(
        self,
        *,
        config: AgentRuntimeConfig,
        device: AudioDevice,
        uploader: ChunkUploader,
        device_resolver: Callable[[], AudioDevice] | None = None,
        ffmpeg_binary: str = "ffmpeg",
        poll_seconds: int = 2,
        heartbeat_seconds: int = 30,
    ) -> None:
        self.config = config
        self.device = device
        self.uploader = uploader
        self.device_resolver = device_resolver
        self.ffmpeg_binary = ffmpeg_binary
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.spool = uploader.spool
        self._process: subprocess.Popen[bytes] | None = None
        self._last_heartbeat_at: datetime | None = None
        self._capture_paused_for_backlog = False
        self._last_capture_observation: tuple[str, int, int] | None = None
        self._last_capture_progress_at: datetime | None = None

    def run(self) -> None:
        active_start = parse_local_time(self.config.active_start_local)
        active_end = parse_local_time(self.config.active_end_local)
        self.spool.ensure()
        previous_handlers: list[tuple[int, object]] = []

        def handle_signal(signum: int, _frame: object) -> None:
            raise CaptureAgentShutdown(signum)

        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers.append((signum, signal.getsignal(signum)))
            signal.signal(signum, handle_signal)

        try:
            while True:
                now = datetime.now().astimezone()
                if is_within_active_window(now=now.time(), start=active_start, end=active_end):
                    self._sync_capture_state(active_window=True)
                    upload_result = self._upload_ready()
                    self._sync_capture_state(active_window=True)
                    self._maybe_heartbeat(uploaded=upload_result.uploaded > 0)
                else:
                    self._sync_capture_state(active_window=False)
                    self._upload_ready()
                    self._maybe_heartbeat(uploaded=False)

                sleep(self.poll_seconds)
        except KeyboardInterrupt:
            LOGGER.info("capture agent interrupted")
        except CaptureAgentShutdown as error:
            LOGGER.info("capture agent received signal %s", error.signum)
        finally:
            for signum, previous_handler in previous_handlers:
                signal.signal(signum, previous_handler)
            self._stop_capture()

    def _ensure_capture_running(self) -> None:
        now = datetime.now(UTC)
        if self._process is not None and self._process.poll() is None:
            if not self._capture_has_stalled(now):
                return

            LOGGER.warning(
                "ffmpeg produced no local spool progress for %ss; restarting source_id=%s",
                CAPTURE_STALL_TIMEOUT_SECONDS,
                self.config.source_id,
            )
            self._stop_capture()

        if self._process is not None and self._process.poll() is not None:
            LOGGER.warning("ffmpeg exited with return code %s; restarting", self._process.returncode)

        device = self._resolve_device()
        self._process = subprocess.Popen(
            build_capture_command(
                device=device,
                spool_dir=self.config.spool_dir,
                ffmpeg_binary=self.ffmpeg_binary,
                segment_seconds=DEFAULT_SEGMENT_SECONDS,
                session_id=f"{self.config.source_id}-{uuid4().hex[:8]}",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._last_capture_observation = self._capture_progress_observation()
        self._last_capture_progress_at = now

    def _resolve_device(self) -> AudioDevice:
        if self.device_resolver is None:
            return self.device

        device = self.device_resolver()
        if device != self.device:
            LOGGER.info(
                "capture device changed source_id=%s from=%s:%s to=%s:%s",
                self.config.source_id,
                self.device.index,
                self.device.name,
                device.index,
                device.name,
            )
        self.device = device
        return device

    def _stop_capture(self) -> None:
        if self._process is None:
            self._last_capture_observation = None
            self._last_capture_progress_at = None
            return

        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=10)

        self._process = None
        self._last_capture_observation = None
        self._last_capture_progress_at = None

    def _sync_capture_state(self, *, active_window: bool) -> None:
        if not active_window:
            self._stop_capture()
            return

        if self.spool.is_backlog_at_capacity():
            self._pause_capture_for_backlog()
            return

        if self._capture_paused_for_backlog:
            LOGGER.info(
                "capture resumed after backlog drained below capacity (%s/%s files)",
                self.spool.backlog_file_count(),
                self.config.max_backlog_files,
            )
            self._capture_paused_for_backlog = False

        self._ensure_capture_running()

    def _pause_capture_for_backlog(self) -> None:
        if not self._capture_paused_for_backlog:
            LOGGER.warning(
                "capture paused due to backlog pressure (%s/%s files); continuing backlog upload retries",
                self.spool.backlog_file_count(),
                self.config.max_backlog_files,
            )
            self._capture_paused_for_backlog = True
        self._stop_capture()

    def _capture_has_stalled(self, now: datetime) -> bool:
        observation = self._capture_progress_observation()
        if observation != self._last_capture_observation:
            self._last_capture_observation = observation
            self._last_capture_progress_at = now
            return False

        if self._last_capture_progress_at is None:
            self._last_capture_progress_at = now
            return False

        stalled_for_seconds = (now - self._last_capture_progress_at).total_seconds()
        return stalled_for_seconds >= CAPTURE_STALL_TIMEOUT_SECONDS

    def _capture_progress_observation(self) -> tuple[str, int, int] | None:
        latest_path: Path | None = None
        latest_mtime_ns = -1
        latest_size = 0
        for candidate in self.config.spool_dir.glob("*.wav"):
            try:
                stat_result = candidate.stat()
            except FileNotFoundError:
                continue
            if stat_result.st_mtime_ns <= latest_mtime_ns:
                continue
            latest_path = candidate
            latest_mtime_ns = stat_result.st_mtime_ns
            latest_size = stat_result.st_size

        if latest_path is None:
            return None

        return (latest_path.name, latest_size, latest_mtime_ns)

    def _upload_ready(self) -> UploadBatchResult:
        try:
            return self.uploader.upload_ready()
        except SpoolBacklogFullError:
            self._pause_capture_for_backlog()
            return UploadBatchResult()

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
    env_file: str | None = None,
) -> None:
    configure_logging()
    config = load_runtime_config(dry_run=dry_run, env_file=env_file)
    spool = LocalSpool(
        config.spool_dir,
        max_backlog_files=config.max_backlog_files,
        require_stable_root=True,
    )
    spool.ensure()
    selection = device_selection or config.capture_device_name

    def resolve_device() -> AudioDevice:
        devices = list_local_audio_devices(ffmpeg_binary)
        return choose_audio_device(devices, selection)

    device = resolve_device()

    if dry_run:
        LOGGER.info("dry-run device=%s", device.name)
        LOGGER.info("dry-run spool_dir=%s", config.spool_dir.resolve())
        LOGGER.info(
            "dry-run active_window=%s-%s",
            config.active_start_local,
            config.active_end_local,
        )
        LOGGER.info("dry-run backlog_cap=%s", config.max_backlog_files)
        print(f"device: {device.index}: {device.name}")
        print(f"spool: {config.spool_dir.resolve()}")
        print(f"active-window: {config.active_start_local} -> {config.active_end_local}")
        print(f"backlog-cap: {config.max_backlog_files}")
        return

    if config.aws_region is None or config.s3_bucket is None or config.database_url is None:
        raise RuntimeError("missing runtime configuration for uploads and database access")

    s3_client = build_s3_client(config.aws_region)
    session_factory = build_session_factory(
        DatabaseSettings(
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
        silence_filter_enabled=config.silence_filter_enabled,
        silence_max_volume_db=config.silence_max_volume_db,
    )
    agent = CaptureAgent(
        config=config,
        device=device,
        uploader=uploader,
        device_resolver=resolve_device,
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


def load_runtime_config(*, dry_run: bool, env_file: str | None = None) -> AgentRuntimeConfig:
    settings = load_settings(CaptureSettings, env_file=env_file)
    source_id = settings.source_id
    source_type = settings.source_type
    spool_dir = Path(settings.spool_dir)
    capture_device_name = settings.capture_device_name
    max_backlog_files = settings.capture_max_backlog_files
    silence_filter_enabled = settings.silence_filter_enabled
    silence_max_volume_db = settings.silence_max_volume_db
    active_start_local = settings.active_start_local
    active_end_local = settings.active_end_local
    device_owner = settings.device_owner

    if dry_run:
        return AgentRuntimeConfig(
            source_id=source_id,
            source_type=source_type,
            device_owner=device_owner,
            spool_dir=spool_dir,
            capture_device_name=capture_device_name,
            max_backlog_files=max_backlog_files,
            active_start_local=active_start_local,
            active_end_local=active_end_local,
            silence_filter_enabled=silence_filter_enabled,
            silence_max_volume_db=silence_max_volume_db,
        )

    required = {
        "AWS_REGION": settings.aws_region,
        "S3_BUCKET": settings.s3_bucket,
        "DATABASE_URL": settings.database_url,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"missing required environment variables: {', '.join(sorted(missing))}")

    return AgentRuntimeConfig(
        source_id=source_id,
        source_type=source_type,
        device_owner=device_owner,
        spool_dir=spool_dir,
        capture_device_name=capture_device_name,
        max_backlog_files=max_backlog_files,
        active_start_local=active_start_local,
        active_end_local=active_end_local,
        silence_filter_enabled=silence_filter_enabled,
        silence_max_volume_db=silence_max_volume_db,
        aws_region=required["AWS_REGION"],
        s3_bucket=required["S3_BUCKET"],
        database_url=required["DATABASE_URL"],
        database_ssl_root_cert=settings.database_ssl_root_cert,
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
