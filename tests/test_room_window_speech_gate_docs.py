from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_env_example_mentions_room_min_speech_seconds() -> None:
    text = _read(".env.example")

    assert "ROOM_MIN_SPEECH_SECONDS" in text


def test_readme_explains_room_window_speech_gate() -> None:
    text = _read("README.md").lower()

    assert "room_min_speech_seconds" in text
    assert "20" in text
    assert "assemblyai" in text
    assert "skip" in text
    assert "30s" in text
    assert "silence filter" in text


def test_smoke_test_doc_mentions_room_window_speech_gate() -> None:
    text = _read("docs/ops/smoke-test.md").lower()

    assert "room_min_speech_seconds" in text
    assert "20" in text
    assert "skip" in text
    assert "assemblyai" in text
