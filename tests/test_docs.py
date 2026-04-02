from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_smoke_test_doc_mentions_capture_worker_api_and_search() -> None:
    text = _read("docs/ops/smoke-test.md")

    assert "agent run --dry-run" in text
    assert "worker run-once --dry-run" in text
    assert "/search" in text
    assert "presigned" in text.lower()


def test_smoke_test_doc_covers_required_operator_flow() -> None:
    text = _read("docs/ops/smoke-test.md").lower()

    assert "macbook" in text
    assert "room-box" in text
    assert "live capture" in text
    assert "postgres" in text
    assert "open" in text and "replay" in text


def test_launchd_templates_exist_for_worker_and_api() -> None:
    worker_text = _read("deploy/launchd/com.ambient-memory.worker.plist")
    api_text = _read("deploy/launchd/com.ambient-memory.api.plist")

    assert "<key>WorkingDirectory</key>" in worker_text
    assert "<key>EnvironmentVariables</key>" in worker_text
    assert "ambient-memory" in worker_text
    assert "worker" in worker_text

    assert "<key>WorkingDirectory</key>" in api_text
    assert "<key>EnvironmentVariables</key>" in api_text
    assert "ambient-memory" in api_text
    assert "api" in api_text
