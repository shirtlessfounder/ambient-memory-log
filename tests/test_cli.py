import io
from datetime import datetime
from pathlib import Path
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
    assert "start-teammate" in help_text
    assert "start-room-mic" in help_text
    assert "start-dual-capture" in help_text
    assert "start-worker" in help_text
    assert "start-api" in help_text


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

    def fake_run_capture_agent(
        *,
        dry_run: bool,
        ffmpeg_binary: str,
        device_selection: str | None,
        env_file,
    ) -> None:
        calls.update(
            {
                "dry_run": dry_run,
                "ffmpeg_binary": ffmpeg_binary,
                "device_selection": device_selection,
                "env_file": env_file,
            }
        )

    monkeypatch.setattr(cli, "run_capture_agent", fake_run_capture_agent)

    result = runner.invoke(app, ["agent", "run", "--dry-run"])

    assert result.exit_code == 0
    assert calls == {
        "dry_run": True,
        "ffmpeg_binary": "ffmpeg",
        "device_selection": None,
        "env_file": None,
    }


def test_cli_start_teammate_uses_teammate_env_file(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_capture_agent(
        *,
        dry_run: bool,
        ffmpeg_binary: str,
        device_selection: str | None,
        env_file,
    ) -> None:
        calls.update(
            {
                "dry_run": dry_run,
                "ffmpeg_binary": ffmpeg_binary,
                "device_selection": device_selection,
                "env_file": env_file,
            }
        )

    monkeypatch.setattr(cli, "run_capture_agent", fake_run_capture_agent)

    result = runner.invoke(app, ["start-teammate", "--dry-run"])

    assert result.exit_code == 0
    assert calls == {
        "dry_run": True,
        "ffmpeg_binary": "ffmpeg",
        "device_selection": None,
        "env_file": ".env.teammate",
    }


def test_cli_start_room_mic_uses_room_env_file(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_capture_agent(
        *,
        dry_run: bool,
        ffmpeg_binary: str,
        device_selection: str | None,
        env_file,
    ) -> None:
        calls.update(
            {
                "dry_run": dry_run,
                "ffmpeg_binary": ffmpeg_binary,
                "device_selection": device_selection,
                "env_file": env_file,
            }
        )

    monkeypatch.setattr(cli, "run_capture_agent", fake_run_capture_agent)

    result = runner.invoke(app, ["start-room-mic", "--dry-run"])

    assert result.exit_code == 0
    assert calls == {
        "dry_run": True,
        "ffmpeg_binary": "ffmpeg",
        "device_selection": None,
        "env_file": ".env.room-mic",
    }


def test_cli_start_dual_capture_wires_expected_child_commands(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_dual_capture(*, child_specs, cwd: Path) -> int:
        calls["child_specs"] = child_specs
        calls["cwd"] = cwd
        return 0

    monkeypatch.setattr(cli, "_run_dual_capture", fake_run_dual_capture)

    with runner.isolated_filesystem():
        Path(".env.teammate").write_text("SOURCE_ID=desk-a\n", encoding="utf-8")
        Path(".env.room-mic").write_text("SOURCE_ID=room-1\n", encoding="utf-8")

        result = runner.invoke(app, ["start-dual-capture"])

        assert result.exit_code == 0
        assert calls["cwd"] == Path.cwd()
        assert [(spec.role, spec.command) for spec in calls["child_specs"]] == [
            ("teammate", ("uv", "run", "ambient-memory", "start-teammate")),
            ("room-mic", ("uv", "run", "ambient-memory", "start-room-mic")),
        ]


def test_cli_start_dual_capture_requires_both_env_files(monkeypatch) -> None:
    from ambient_memory import cli

    called = False

    def fake_run_dual_capture(*, child_specs, cwd: Path) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "_run_dual_capture", fake_run_dual_capture)

    with runner.isolated_filesystem():
        Path(".env.teammate").write_text("SOURCE_ID=desk-a\n", encoding="utf-8")

        result = runner.invoke(app, ["start-dual-capture"])

    assert result.exit_code == 1
    assert ".env.room-mic" in result.output
    assert called is False


def test_run_dual_capture_stops_sibling_when_child_exits(monkeypatch, tmp_path: Path) -> None:
    from ambient_memory import cli

    class FakeProcess:
        def __init__(self, returncodes: list[int | None], *, pid: int) -> None:
            self.pid = pid
            self.returncodes = list(returncodes)
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.terminated = False
            self.killed = False

        def poll(self) -> int | None:
            if self.returncodes:
                value = self.returncodes.pop(0)
                if value is not None:
                    self.returncode = value
                return value
            return getattr(self, "returncode", None)

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            return getattr(self, "returncode", 0)

    processes = [FakeProcess([None, None], pid=101), FakeProcess([7], pid=202)]
    popen_calls: list[tuple[tuple[str, ...], str, bool]] = []
    process_group_signals: list[tuple[int, int]] = []

    def fake_popen(command, *, cwd, stdout, stderr, text, bufsize, start_new_session):
        popen_calls.append((tuple(command), cwd, start_new_session))
        return processes[len(popen_calls) - 1]

    monkeypatch.setattr(cli, "_start_process_output_threads", lambda role, process: [])
    monkeypatch.setattr(cli.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(cli.os, "killpg", lambda pgid, signum: process_group_signals.append((pgid, signum)))

    exit_code = cli._run_dual_capture(
        child_specs=(
            cli.DualCaptureChildSpec("teammate", ("uv", "run", "ambient-memory", "start-teammate")),
            cli.DualCaptureChildSpec("room-mic", ("uv", "run", "ambient-memory", "start-room-mic")),
        ),
        cwd=tmp_path,
        popen_factory=fake_popen,
        sleep_fn=lambda _: None,
    )

    assert exit_code == 7
    assert popen_calls == [
        (("uv", "run", "ambient-memory", "start-teammate"), str(tmp_path), True),
        (("uv", "run", "ambient-memory", "start-room-mic"), str(tmp_path), True),
    ]
    assert process_group_signals == [(101, cli.signal.SIGTERM)]
    assert processes[0].killed is False


def test_run_dual_capture_stops_both_children_on_interrupt(monkeypatch, tmp_path: Path) -> None:
    from ambient_memory import cli

    class FakeProcess:
        def __init__(self, *, pid: int) -> None:
            self.pid = pid
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.terminated = False
            self.killed = False

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout: float | None = None) -> int:
            return getattr(self, "returncode", 0)

    processes = [FakeProcess(pid=101), FakeProcess(pid=202)]
    started_processes: list[FakeProcess] = []
    process_group_signals: list[tuple[int, int]] = []

    def fake_popen(command, *, cwd, stdout, stderr, text, bufsize, start_new_session):
        process = processes.pop(0)
        started_processes.append(process)
        assert start_new_session is True
        return process

    def fake_sleep(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_start_process_output_threads", lambda role, process: [])
    monkeypatch.setattr(cli.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(cli.os, "killpg", lambda pgid, signum: process_group_signals.append((pgid, signum)))

    exit_code = cli._run_dual_capture(
        child_specs=(
            cli.DualCaptureChildSpec("teammate", ("uv", "run", "ambient-memory", "start-teammate")),
            cli.DualCaptureChildSpec("room-mic", ("uv", "run", "ambient-memory", "start-room-mic")),
        ),
        cwd=tmp_path,
        popen_factory=fake_popen,
        sleep_fn=fake_sleep,
    )

    assert exit_code == 130
    assert len(started_processes) == 2
    assert process_group_signals == [
        (101, cli.signal.SIGTERM),
        (202, cli.signal.SIGTERM),
    ]


def test_stop_child_processes_targets_process_groups(monkeypatch) -> None:
    from ambient_memory import cli

    class FakeProcess:
        def __init__(self, *, pid: int) -> None:
            self.pid = pid
            self.returncode = None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            raise AssertionError("terminate should not be used when process group signaling succeeds")

        def kill(self) -> None:
            raise AssertionError("kill should not be used when process group signaling succeeds")

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = -15
            return self.returncode

    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(cli.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(cli.os, "killpg", lambda pgid, signum: signals.append((pgid, signum)))

    cli._stop_child_processes([FakeProcess(pid=321)])

    assert signals == [(321, cli.signal.SIGTERM)]


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

    def fake_run_worker(*, poll_seconds: float, env_file) -> None:
        calls["poll_seconds"] = poll_seconds
        calls["env_file"] = env_file

    monkeypatch.setattr(cli, "run_worker_loop", fake_run_worker)

    result = runner.invoke(app, ["worker", "run", "--poll-seconds", "2.5"])

    assert result.exit_code == 0
    assert calls == {"poll_seconds": 2.5, "env_file": None}


def test_cli_start_worker_uses_worker_env_file(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_worker(*, poll_seconds: float, env_file) -> None:
        calls["poll_seconds"] = poll_seconds
        calls["env_file"] = env_file

    monkeypatch.setattr(cli, "run_worker_loop", fake_run_worker)

    result = runner.invoke(app, ["start-worker", "--poll-seconds", "2.5"])

    assert result.exit_code == 0
    assert calls == {"poll_seconds": 2.5, "env_file": ".env.worker"}


def test_cli_api_without_subcommand_starts_server(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_api_server(*, host: str | None = None, port: int | None = None, env_file=None) -> None:
        calls["host"] = host
        calls["port"] = port
        calls["env_file"] = env_file

    monkeypatch.setattr(cli, "run_api_server", fake_run_api_server)

    result = runner.invoke(app, ["api", "--host", "0.0.0.0", "--port", "9001"])

    assert result.exit_code == 0
    assert calls == {"host": "0.0.0.0", "port": 9001, "env_file": None}


def test_cli_api_run_alias_still_wires_host_and_port(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_api_server(*, host: str | None = None, port: int | None = None, env_file=None) -> None:
        calls["host"] = host
        calls["port"] = port
        calls["env_file"] = env_file

    monkeypatch.setattr(cli, "run_api_server", fake_run_api_server)

    result = runner.invoke(app, ["api", "run", "--host", "0.0.0.0", "--port", "9001"])

    assert result.exit_code == 0
    assert calls == {"host": "0.0.0.0", "port": 9001, "env_file": None}


def test_cli_start_api_uses_api_env_file(monkeypatch) -> None:
    from ambient_memory import cli

    calls: dict[str, object] = {}

    def fake_run_api_server(*, host: str | None = None, port: int | None = None, env_file=None) -> None:
        calls["host"] = host
        calls["port"] = port
        calls["env_file"] = env_file

    monkeypatch.setattr(cli, "run_api_server", fake_run_api_server)

    result = runner.invoke(app, ["start-api", "--host", "0.0.0.0", "--port", "9001"])

    assert result.exit_code == 0
    assert calls == {"host": "0.0.0.0", "port": 9001, "env_file": ".env.api"}


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

    def fake_load_settings(settings_type: object, *, env_file: str | None = None) -> FakeEnrollmentSettings:
        calls["load_settings"] = {
            "settings_type": settings_type,
            "env_file": env_file,
        }
        return FakeEnrollmentSettings()

    monkeypatch.setattr(cli, "load_settings", fake_load_settings)
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
    assert calls["load_settings"] == {
        "settings_type": cli.EnrollmentSettings,
        "env_file": ".env.teammate",
    }
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

    class FakeEnrollmentSettings:
        pyannote_api_key = "secret"
        database_url = "postgresql://example"
        database_ssl_root_cert = None

    class Result:
        speaker_label = "Dylan"
        sample_path = "/tmp/dylan.wav"
        replaced_existing = True

    def fake_load_settings(settings_type: object, *, env_file: str | None = None) -> FakeEnrollmentSettings:
        calls["load_settings"] = {
            "settings_type": settings_type,
            "env_file": env_file,
        }
        return FakeEnrollmentSettings()

    def fake_run_live_voiceprint_enrollment(
        *,
        label: str,
        device_selection: str | None,
        ffmpeg_binary: str,
        settings: object,
    ):
        calls.update(
            {
                "label": label,
                "device_selection": device_selection,
                "ffmpeg_binary": ffmpeg_binary,
                "settings": settings,
            }
        )
        return Result()

    monkeypatch.setattr(cli, "load_settings", fake_load_settings)
    monkeypatch.setattr(cli, "run_live_voiceprint_enrollment", fake_run_live_voiceprint_enrollment)

    result = runner.invoke(
        app,
        ["enroll", "voiceprint-live", "--label", "Dylan", "--device", "Built-in Microphone"],
    )

    assert result.exit_code == 0
    assert calls == {
        "load_settings": {
            "settings_type": cli.EnrollmentSettings,
            "env_file": ".env.teammate",
        },
        "label": "Dylan",
        "device_selection": "Built-in Microphone",
        "ffmpeg_binary": "ffmpeg",
        "settings": calls["settings"],
    }
    assert isinstance(calls["settings"], FakeEnrollmentSettings)
    assert "Updated voiceprint for Dylan" in result.output


def test_cli_enroll_voiceprint_live_help_lists_required_options() -> None:
    result = runner.invoke(app, ["enroll", "voiceprint-live", "--help"])

    assert result.exit_code == 0
    assert "--label" in result.output
    assert "--device" in result.output
