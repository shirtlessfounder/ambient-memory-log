from datetime import datetime
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from ambient_memory.capture.device_discovery import AudioDevice
from ambient_memory.cli import app


runner = CliRunner()


def test_cli_lists_expected_commands() -> None:
    help_text = app.get_help()

    assert "agent" in help_text
    assert "worker" in help_text
    assert "api" in help_text
    assert "enroll" in help_text
    assert "import-recording" in help_text
    assert "list-devices" in help_text


def test_cli_list_devices_renders_detected_inputs(monkeypatch) -> None:
    from ambient_memory import cli

    monkeypatch.setattr(
        cli,
        "list_local_audio_devices",
        lambda ffmpeg_binary="ffmpeg": [AudioDevice(index="0", name="Built-in Microphone")],
    )

    result = runner.invoke(app, ["list-devices"])

    assert result.exit_code == 0
    assert "0: Built-in Microphone" in result.output


def test_cli_agent_run_wires_dry_run_flag(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_capture_agent(*, dry_run: bool, ffmpeg_binary: str, device_selection: str | None) -> None:
        calls.update(
            {
                "dry_run": dry_run,
                "ffmpeg_binary": ffmpeg_binary,
                "device_selection": device_selection,
            }
        )

    monkeypatch.setattr(cli, "run_capture_agent", fake_run_capture_agent)

    result = runner.invoke(app, ["agent", "run", "--dry-run"])

    assert result.exit_code == 0
    assert calls == {
        "dry_run": True,
        "ffmpeg_binary": "ffmpeg",
        "device_selection": None,
    }


def test_cli_worker_run_once_wires_dry_run_flag(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    class Result:
        dry_run = True
        pending_chunks = 3
        windows = 2
        processed_chunks = 0
        failed_chunks = 0

    def fake_run_worker_once(*, dry_run: bool):
        calls["dry_run"] = dry_run
        return Result()

    monkeypatch.setattr(cli, "run_worker_once", fake_run_worker_once)

    result = runner.invoke(app, ["worker", "run-once", "--dry-run"])

    assert result.exit_code == 0
    assert calls == {"dry_run": True}
    assert "pending" in result.output.lower()


def test_cli_worker_run_wires_poll_seconds(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_worker(*, poll_seconds: float) -> None:
        calls["poll_seconds"] = poll_seconds

    monkeypatch.setattr(cli, "run_worker_loop", fake_run_worker)

    result = runner.invoke(app, ["worker", "run", "--poll-seconds", "2.5"])

    assert result.exit_code == 0
    assert calls == {"poll_seconds": 2.5}


def test_cli_api_without_subcommand_starts_server(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_api_server(*, host: str | None = None, port: int | None = None) -> None:
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr(cli, "run_api_server", fake_run_api_server)

    result = runner.invoke(app, ["api", "--host", "0.0.0.0", "--port", "9001"])

    assert result.exit_code == 0
    assert calls == {"host": "0.0.0.0", "port": 9001}


def test_cli_api_run_alias_still_wires_host_and_port(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_api_server(*, host: str | None = None, port: int | None = None) -> None:
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr(cli, "run_api_server", fake_run_api_server)

    result = runner.invoke(app, ["api", "run", "--host", "0.0.0.0", "--port", "9001"])

    assert result.exit_code == 0
    assert calls == {"host": "0.0.0.0", "port": 9001}


def test_cli_enroll_voiceprint_help_lists_required_options() -> None:
    result = runner.invoke(app, ["enroll", "voiceprint", "--help"])

    assert result.exit_code == 0
    assert "--label" in result.output
    assert "--audio" in result.output


def test_cli_enroll_voiceprint_creates_voiceprint(monkeypatch, tmp_path) -> None:
    from contextlib import contextmanager

    from ambient_memory import cli

    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"audio-bytes")

    calls: dict[str, object] = {}

    class FakeEnrollmentSettings:
        pyannote_api_key = "secret"
        database_url = "postgresql://example"
        database_ssl_root_cert = None

    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            calls["api_key"] = api_key

        def enroll_voiceprint(self, *, label: str, audio_bytes: bytes, filename: str) -> str:
            calls["label"] = label
            calls["audio_bytes"] = audio_bytes
            calls["filename"] = filename
            return "vp_123"

    @contextmanager
    def fake_session_scope(settings: object):
        calls["session_settings"] = settings
        yield object()

    def fake_create_voiceprint(session: object, *, speaker_label: str, provider_voiceprint_id: str, source_audio_key: str | None):
        calls["saved"] = {
            "session": session,
            "speaker_label": speaker_label,
            "provider_voiceprint_id": provider_voiceprint_id,
            "source_audio_key": source_audio_key,
        }
        return object()

    monkeypatch.setattr(cli, "EnrollmentSettings", lambda: FakeEnrollmentSettings())
    monkeypatch.setattr(cli, "PyannoteClient", FakeClient)
    monkeypatch.setattr(cli, "session_scope", fake_session_scope)
    monkeypatch.setattr(cli, "create_voiceprint", fake_create_voiceprint)

    result = runner.invoke(app, ["enroll", "voiceprint", "--label", "Dylan", "--audio", str(audio_path)])

    assert result.exit_code == 0
    assert "Created voiceprint for Dylan" in result.output
    assert calls["api_key"] == "secret"
    assert calls["label"] == "Dylan"
    assert calls["audio_bytes"] == b"audio-bytes"
    assert calls["filename"] == "sample.wav"
    assert calls["saved"] == {
        "session": calls["saved"]["session"],
        "speaker_label": "Dylan",
        "provider_voiceprint_id": "vp_123",
        "source_audio_key": str(audio_path),
    }


def test_cli_import_recording_wires_args(monkeypatch, tmp_path) -> None:
    from ambient_memory import cli

    recording_path = tmp_path / "Friday Sync.m4a"
    recording_path.write_bytes(b"audio-bytes")

    calls: dict[str, object] = {}

    class Result:
        source_id = "friday-sync"
        chunk_count = 2
        uploaded = 2
        failed = 0

    def fake_run_recording_import(
        *,
        recording_path,
        start,
        source_id,
        ffmpeg_binary,
    ):
        calls.update(
            {
                "recording_path": recording_path,
                "start": start,
                "source_id": source_id,
                "ffmpeg_binary": ffmpeg_binary,
            }
        )
        return Result()

    monkeypatch.setattr(cli, "run_recording_import", fake_run_recording_import)
    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: False)
    monkeypatch.setattr(cli, "_count_existing_chunks_for_source_id", lambda source_id: 0)

    result = runner.invoke(
        app,
        [
            "import-recording",
            str(recording_path),
            "--start",
            "2026-04-03 09:00",
            "--source-id",
            "friday-sync",
        ],
    )

    assert result.exit_code == 0
    assert calls == {
        "recording_path": recording_path.resolve(),
        "start": "2026-04-03 09:00",
        "source_id": "friday-sync",
        "ffmpeg_binary": "ffmpeg",
    }
    assert "Imported 2 chunk(s) as friday-sync" in result.output


def test_cli_import_recording_interactive_runs_worker_by_default(monkeypatch, tmp_path) -> None:
    from ambient_memory import cli

    recording_path = tmp_path / "Friday Sync.m4a"
    recording_path.write_bytes(b"audio-bytes")

    calls: dict[str, object] = {}

    class ImportResult:
        source_id = "friday-sync"
        chunk_count = 2
        uploaded = 2
        failed = 0

    class WorkerResult:
        dry_run = False
        pending_chunks = 0
        windows = 1
        processed_chunks = 2
        failed_chunks = 0

    def fake_run_recording_import(*, recording_path, start, source_id, ffmpeg_binary):
        calls["import"] = {
            "recording_path": recording_path,
            "start": start,
            "source_id": source_id,
            "ffmpeg_binary": ffmpeg_binary,
        }
        return ImportResult()

    def fake_run_worker_once(*, dry_run: bool):
        calls["worker_dry_run"] = dry_run
        return WorkerResult()

    monkeypatch.setattr(cli, "run_recording_import", fake_run_recording_import)
    monkeypatch.setattr(cli, "run_worker_once", fake_run_worker_once)
    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli, "_count_existing_chunks_for_source_id", lambda source_id: 0)
    monkeypatch.setattr(
        cli,
        "_now_local",
        lambda: datetime(2026, 4, 3, 14, 30, tzinfo=ZoneInfo("America/New_York")),
    )

    result = runner.invoke(
        app,
        ["import-recording", str(recording_path), "--start", "2026-04-03 09:00"],
        input="\n",
    )

    assert result.exit_code == 0
    assert calls["worker_dry_run"] is False
    assert "Estimated worker time: ~1 minute" in result.output
    assert "Estimated completion: ~2:31 PM EDT" in result.output
    assert "Run worker now? [Y/n]:" in result.output
    assert "Processed 2 chunk(s) across 1 window(s); failed 0" in result.output


def test_cli_import_recording_interactive_skips_worker_on_no(monkeypatch, tmp_path) -> None:
    from ambient_memory import cli

    recording_path = tmp_path / "Friday Sync.m4a"
    recording_path.write_bytes(b"audio-bytes")

    calls: dict[str, object] = {"worker_called": False}

    class ImportResult:
        source_id = "friday-sync"
        chunk_count = 2
        uploaded = 2
        failed = 0

    def fake_run_recording_import(*, recording_path, start, source_id, ffmpeg_binary):
        calls["import"] = True
        return ImportResult()

    def fake_run_worker_once(*, dry_run: bool):
        calls["worker_called"] = True
        raise AssertionError("worker should not run")

    monkeypatch.setattr(cli, "run_recording_import", fake_run_recording_import)
    monkeypatch.setattr(cli, "run_worker_once", fake_run_worker_once)
    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli, "_count_existing_chunks_for_source_id", lambda source_id: 0)
    monkeypatch.setattr(
        cli,
        "_now_local",
        lambda: datetime(2026, 4, 3, 14, 30, tzinfo=ZoneInfo("America/New_York")),
    )

    result = runner.invoke(
        app,
        ["import-recording", str(recording_path), "--start", "2026-04-03 09:00"],
        input="n\n",
    )

    assert result.exit_code == 0
    assert calls["worker_called"] is False
    assert "Estimated worker time: ~1 minute" in result.output
    assert "Estimated completion: ~2:31 PM EDT" in result.output
    assert "Run worker now? [Y/n]:" in result.output


def test_worker_estimate_formats_range_and_local_eta() -> None:
    from ambient_memory.cli import _estimate_worker_runtime_summary

    summary = _estimate_worker_runtime_summary(
        219,
        now=datetime(2026, 4, 3, 14, 30, tzinfo=ZoneInfo("America/New_York")),
    )

    assert summary.duration_text == "~25-50 minutes"
    assert summary.completion_text == "~2:55 PM to 3:20 PM EDT"


def test_cli_import_recording_existing_source_non_interactive_fails(monkeypatch, tmp_path) -> None:
    from ambient_memory import cli

    recording_path = tmp_path / "Friday Sync.m4a"
    recording_path.write_bytes(b"audio-bytes")

    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: False)
    monkeypatch.setattr(cli, "_count_existing_chunks_for_source_id", lambda source_id: 5)
    monkeypatch.setattr(
        cli,
        "run_recording_import",
        lambda **_: (_ for _ in ()).throw(AssertionError("import should not run")),
    )

    result = runner.invoke(
        app,
        [
            "import-recording",
            str(recording_path),
            "--start",
            "2026-04-03 09:00",
            "--source-id",
            "friday-sync",
        ],
    )

    assert result.exit_code == 1
    assert "Source id friday-sync already has 5 chunk(s)." in result.output
    assert "--allow-existing-source-id" in result.output


def test_cli_import_recording_existing_source_interactive_defaults_to_abort(monkeypatch, tmp_path) -> None:
    from ambient_memory import cli

    recording_path = tmp_path / "Friday Sync.m4a"
    recording_path.write_bytes(b"audio-bytes")

    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli, "_count_existing_chunks_for_source_id", lambda source_id: 5)
    monkeypatch.setattr(
        cli,
        "run_recording_import",
        lambda **_: (_ for _ in ()).throw(AssertionError("import should not run")),
    )

    result = runner.invoke(
        app,
        ["import-recording", str(recording_path), "--start", "2026-04-03 09:00"],
        input="\n",
    )

    assert result.exit_code == 1
    assert "Source id friday-sync already has 5 chunk(s)." in result.output
    assert "Continue and append? [y/N]:" in result.output
    assert "Import cancelled." in result.output


def test_cli_import_recording_existing_source_interactive_yes_continues(monkeypatch, tmp_path) -> None:
    from ambient_memory import cli

    recording_path = tmp_path / "Friday Sync.m4a"
    recording_path.write_bytes(b"audio-bytes")

    calls: dict[str, object] = {}

    class Result:
        source_id = "friday-sync"
        chunk_count = 0
        uploaded = 0
        failed = 0

    def fake_run_recording_import(*, recording_path, start, source_id, ffmpeg_binary):
        calls["import"] = {
            "recording_path": recording_path,
            "start": start,
            "source_id": source_id,
            "ffmpeg_binary": ffmpeg_binary,
        }
        return Result()

    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli, "_count_existing_chunks_for_source_id", lambda source_id: 5)
    monkeypatch.setattr(cli, "run_recording_import", fake_run_recording_import)

    result = runner.invoke(
        app,
        ["import-recording", str(recording_path), "--start", "2026-04-03 09:00"],
        input="y\n",
    )

    assert result.exit_code == 0
    assert calls["import"] == {
        "recording_path": recording_path.resolve(),
        "start": "2026-04-03 09:00",
        "source_id": "friday-sync",
        "ffmpeg_binary": "ffmpeg",
    }
    assert "Continue and append? [y/N]:" in result.output
    assert "Imported 0 chunk(s) as friday-sync" in result.output


def test_cli_import_recording_existing_source_override_skips_guard(monkeypatch, tmp_path) -> None:
    from ambient_memory import cli

    recording_path = tmp_path / "Friday Sync.m4a"
    recording_path.write_bytes(b"audio-bytes")

    calls: dict[str, object] = {"count_called": False}

    class Result:
        source_id = "friday-sync"
        chunk_count = 0
        uploaded = 0
        failed = 0

    def fake_run_recording_import(*, recording_path, start, source_id, ffmpeg_binary):
        calls["import"] = {
            "recording_path": recording_path,
            "start": start,
            "source_id": source_id,
            "ffmpeg_binary": ffmpeg_binary,
        }
        return Result()

    def fail_if_counted(source_id: str) -> int:
        calls["count_called"] = True
        raise AssertionError("count should not run")

    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: False)
    monkeypatch.setattr(cli, "_count_existing_chunks_for_source_id", fail_if_counted)
    monkeypatch.setattr(cli, "run_recording_import", fake_run_recording_import)

    result = runner.invoke(
        app,
        [
            "import-recording",
            str(recording_path),
            "--start",
            "2026-04-03 09:00",
            "--allow-existing-source-id",
        ],
    )

    assert result.exit_code == 0
    assert calls["count_called"] is False
    assert calls["import"] == {
        "recording_path": recording_path.resolve(),
        "start": "2026-04-03 09:00",
        "source_id": "friday-sync",
        "ffmpeg_binary": "ffmpeg",
    }


def test_cli_import_recording_existing_derived_source_id_fails(monkeypatch, tmp_path) -> None:
    from ambient_memory import cli

    recording_path = tmp_path / "Friday Sync.m4a"
    recording_path.write_bytes(b"audio-bytes")

    def fake_count_existing_chunks(source_id: str) -> int:
        assert source_id == "friday-sync"
        return 3

    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: False)
    monkeypatch.setattr(cli, "_count_existing_chunks_for_source_id", fake_count_existing_chunks)
    monkeypatch.setattr(
        cli,
        "run_recording_import",
        lambda **_: (_ for _ in ()).throw(AssertionError("import should not run")),
    )

    result = runner.invoke(
        app,
        ["import-recording", str(recording_path), "--start", "2026-04-03 09:00"],
    )

    assert result.exit_code == 1
    assert "Source id friday-sync already has 3 chunk(s)." in result.output


def test_cli_enroll_voiceprint_live_wires_args(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    class Result:
        speaker_label = "Dylan"
        sample_path = "/tmp/dylan.wav"
        replaced_existing = True

    def fake_run_live_voiceprint_enrollment(*, label: str, device_selection: str | None, ffmpeg_binary: str):
        calls.update(
            {
                "label": label,
                "device_selection": device_selection,
                "ffmpeg_binary": ffmpeg_binary,
            }
        )
        return Result()

    monkeypatch.setattr(cli, "run_live_voiceprint_enrollment", fake_run_live_voiceprint_enrollment)

    result = runner.invoke(
        app,
        ["enroll", "voiceprint-live", "--label", "Dylan", "--device", "Built-in Microphone"],
    )

    assert result.exit_code == 0
    assert calls == {
        "label": "Dylan",
        "device_selection": "Built-in Microphone",
        "ffmpeg_binary": "ffmpeg",
    }
    assert "Updated voiceprint for Dylan" in result.output


def test_cli_enroll_voiceprint_live_help_lists_required_options() -> None:
    result = runner.invoke(app, ["enroll", "voiceprint-live", "--help"])

    assert result.exit_code == 0
    assert "--label" in result.output
    assert "--device" in result.output
