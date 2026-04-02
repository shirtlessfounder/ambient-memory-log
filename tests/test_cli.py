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
