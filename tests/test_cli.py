from ambient_memory.cli import app


def test_cli_lists_expected_commands():
    help_text = app.get_help()

    assert "agent" in help_text
    assert "worker" in help_text
    assert "api" in help_text
