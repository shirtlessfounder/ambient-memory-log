from typer import Typer
from typer.testing import CliRunner


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
def list_devices() -> None:
    """List local capture devices."""


def main() -> None:
    app()
