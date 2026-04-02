from typer.testing import CliRunner

from ambient_memory.capture.device_discovery import AudioDevice
from ambient_memory.cli import app


runner = CliRunner()


def test_cli_lists_expected_commands() -> None:
    help_text = app.get_help()

    assert "agent" in help_text
    assert "worker" in help_text
    assert "api" in help_text
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
