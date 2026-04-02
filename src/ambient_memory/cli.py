from typer import Option, Typer
from typer.testing import CliRunner

from ambient_memory.capture.agent import list_local_audio_devices, run_capture_agent


class HelpTyper(Typer):
    def get_help(self) -> str:
        runner = CliRunner()
        result = runner.invoke(self, ["--help"])
        return result.output


app = HelpTyper(help="Ambient memory log CLI.")
agent_app = Typer(help="Capture agent commands.")
worker_app = Typer(help="Pipeline worker commands.")
api_app = Typer(help="Read API commands.")
enroll_app = Typer(help="Enrollment commands.")

app.add_typer(agent_app, name="agent")
app.add_typer(worker_app, name="worker")
app.add_typer(api_app, name="api")
app.add_typer(enroll_app, name="enroll")


@app.command("list-devices")
def list_devices(
    ffmpeg_binary: str = Option("ffmpeg", help="Path to the ffmpeg binary."),
) -> None:
    """List local capture devices."""
    for device in list_local_audio_devices(ffmpeg_binary=ffmpeg_binary):
        print(f"{device.index}: {device.name}")


@agent_app.command("run")
def agent_run(
    dry_run: bool = Option(False, "--dry-run", help="Log configuration without recording or uploading."),
    ffmpeg_binary: str = Option("ffmpeg", help="Path to the ffmpeg binary."),
    device_selection: str | None = Option(None, "--device", help="Audio device name or index."),
) -> None:
    """Run the local capture agent."""
    run_capture_agent(
        dry_run=dry_run,
        ffmpeg_binary=ffmpeg_binary,
        device_selection=device_selection,
    )


def main() -> None:
    app()
