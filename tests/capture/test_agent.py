import logging
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from ambient_memory.capture.agent import AgentRuntimeConfig, CaptureAgent, load_runtime_config
from ambient_memory.capture.spool import SpoolBacklogFullError
from ambient_memory.capture.uploader import UploadBatchResult


def _clear_agent_env(monkeypatch) -> None:
    for name in (
        "SOURCE_ID",
        "SOURCE_TYPE",
        "DEVICE_OWNER",
        "SPOOL_DIR",
        "ACTIVE_START_LOCAL",
        "ACTIVE_END_LOCAL",
        "AWS_REGION",
        "S3_BUCKET",
        "DATABASE_URL",
        "DATABASE_SSL_ROOT_CERT",
        "CAPTURE_MAX_BACKLOG_FILES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_load_runtime_config_dry_run_reads_capture_settings_from_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "SOURCE_ID=room-1",
                "SOURCE_TYPE=room",
                "DEVICE_OWNER=conference-room",
                "SPOOL_DIR=./spool/room-1",
                "ACTIVE_START_LOCAL=08:30",
                "ACTIVE_END_LOCAL=22:15",
                "CAPTURE_MAX_BACKLOG_FILES=512",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_agent_env(monkeypatch)

    config = load_runtime_config(dry_run=True)

    assert config.source_id == "room-1"
    assert config.source_type == "room"
    assert config.device_owner == "conference-room"
    assert config.spool_dir == Path("./spool/room-1")
    assert config.active_start_local == "08:30"
    assert config.active_end_local == "22:15"
    assert config.max_backlog_files == 512


def test_load_runtime_config_non_dry_run_reads_required_settings_from_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "SOURCE_ID=desk-a",
                "SOURCE_TYPE=macbook",
                "DEVICE_OWNER=dylan",
                "SPOOL_DIR=./spool/desk-a",
                "ACTIVE_START_LOCAL=09:00",
                "ACTIVE_END_LOCAL=00:00",
                "AWS_REGION=us-east-1",
                "S3_BUCKET=ambient-memory",
                "DATABASE_URL=postgresql://db.example/app",
                "DATABASE_SSL_ROOT_CERT=/tmp/rds.pem",
                "CAPTURE_MAX_BACKLOG_FILES=1024",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_agent_env(monkeypatch)

    config = load_runtime_config(dry_run=False)

    assert config.source_id == "desk-a"
    assert config.source_type == "macbook"
    assert config.device_owner == "dylan"
    assert config.spool_dir == Path("./spool/desk-a")
    assert config.aws_region == "us-east-1"
    assert config.s3_bucket == "ambient-memory"
    assert config.database_url == "postgresql://db.example/app"
    assert config.database_ssl_root_cert == "/tmp/rds.pem"
    assert config.max_backlog_files == 1024


@pytest.mark.parametrize("raw_value", ["0", "-1"])
def test_load_runtime_config_rejects_non_positive_backlog_capacity(
    tmp_path: Path,
    monkeypatch,
    raw_value: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "SOURCE_ID=desk-a",
                "SOURCE_TYPE=macbook",
                "DEVICE_OWNER=dylan",
                "SPOOL_DIR=./spool/desk-a",
                "ACTIVE_START_LOCAL=09:00",
                "ACTIVE_END_LOCAL=00:00",
                f"CAPTURE_MAX_BACKLOG_FILES={raw_value}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _clear_agent_env(monkeypatch)

    with pytest.raises(ValidationError) as exc_info:
        load_runtime_config(dry_run=True)

    errors = exc_info.value.errors(include_url=False)

    assert len(errors) == 1
    assert errors[0]["type"] == "greater_than"
    assert errors[0]["loc"] == ("CAPTURE_MAX_BACKLOG_FILES",)
    assert errors[0]["msg"] == "Input should be greater than 0"
    assert errors[0]["ctx"] == {"gt": 0}


class StubSpool:
    def __init__(self, *, full: bool, count: int, max_backlog_files: int) -> None:
        self.full = full
        self.count = count
        self.max_backlog_files = max_backlog_files
        self.ensure_calls = 0

    def ensure(self) -> None:
        self.ensure_calls += 1

    def backlog_file_count(self) -> int:
        return self.count

    def is_backlog_at_capacity(self) -> bool:
        return self.full


class RaisingUploader:
    def __init__(self, spool: StubSpool) -> None:
        self.spool = spool

    def upload_ready(self) -> UploadBatchResult:
        raise SpoolBacklogFullError("retry backlog is full")


class DrainingUploader:
    def __init__(self, spool: StubSpool) -> None:
        self.spool = spool
        self.calls = 0

    def upload_ready(self) -> UploadBatchResult:
        self.calls += 1
        if self.calls == 1:
            self.spool.full = False
            self.spool.count = 3
            return UploadBatchResult(attempted=1, uploaded=1, failed=0)
        return UploadBatchResult(attempted=0, uploaded=0, failed=0)


def _build_agent(uploader) -> CaptureAgent:
    return CaptureAgent(
        config=AgentRuntimeConfig(
            source_id="desk-a",
            source_type="macbook",
            device_owner="dylan",
            spool_dir=Path("/tmp/spool"),
            active_start_local="00:00",
            active_end_local="00:00",
            max_backlog_files=uploader.spool.max_backlog_files,
        ),
        device=SimpleNamespace(index="0", name="Built-in Microphone"),
        uploader=uploader,
    )


def test_capture_agent_handles_backlog_full_errors_without_crashing(monkeypatch, caplog) -> None:
    spool = StubSpool(full=True, count=4, max_backlog_files=4)
    agent = _build_agent(RaisingUploader(spool))
    events: list[str] = []

    monkeypatch.setattr(agent, "_ensure_capture_running", lambda: events.append("start"))
    monkeypatch.setattr(agent, "_stop_capture", lambda: events.append("stop"))
    monkeypatch.setattr(agent, "_maybe_heartbeat", lambda *, uploaded: None)

    sleep_calls = {"count": 0}

    def fake_sleep(_: int) -> None:
        sleep_calls["count"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr("ambient_memory.capture.agent.sleep", fake_sleep)
    caplog.set_level(logging.WARNING)

    agent.run()

    assert "paused due to backlog pressure" in caplog.text
    assert "stop" in events


def test_capture_agent_pauses_and_resumes_capture_based_on_backlog_pressure(
    monkeypatch,
    caplog,
) -> None:
    spool = StubSpool(full=True, count=4, max_backlog_files=4)
    agent = _build_agent(DrainingUploader(spool))
    events: list[str] = []

    monkeypatch.setattr(agent, "_ensure_capture_running", lambda: events.append("start"))
    monkeypatch.setattr(agent, "_stop_capture", lambda: events.append("stop"))
    monkeypatch.setattr(agent, "_maybe_heartbeat", lambda *, uploaded: None)

    sleep_calls = {"count": 0}

    def fake_sleep(_: int) -> None:
        sleep_calls["count"] += 1
        if sleep_calls["count"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("ambient_memory.capture.agent.sleep", fake_sleep)
    caplog.set_level(logging.INFO)

    agent.run()

    assert "paused due to backlog pressure" in caplog.text
    assert "resumed after backlog drained" in caplog.text
    assert "stop" in events
    assert "start" in events
    assert events.index("start") > events.index("stop")
