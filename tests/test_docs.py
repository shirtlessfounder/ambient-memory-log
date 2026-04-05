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


def test_operator_setup_doc_mentions_env_launchctl_and_logs() -> None:
    text = _read("docs/operator-setup.md").lower()

    assert ".env" in text
    assert "capture_device_name" in text
    assert "launchctl" in text
    assert "log" in text


def test_operator_setup_doc_covers_install_validation_and_service_flow() -> None:
    text = _read("docs/operator-setup.md")

    assert "uv sync" in text
    assert "ffmpeg" in text
    assert "ambient-memory list-devices" in text
    assert "ambient-memory agent run --dry-run" in text
    assert "launchctl bootstrap" in text
    assert "launchctl bootout" in text


def test_capture_agent_launchd_template_references_wrapper_script() -> None:
    text = _read("deploy/launchd/com.ambient-memory.capture-agent.plist")

    assert "start-capture-agent.sh" in text
    assert "<key>RunAtLoad</key>" in text
    assert "<key>KeepAlive</key>" in text


def test_env_example_mentions_capture_device_name() -> None:
    text = _read(".env.example")

    assert "CAPTURE_DEVICE_NAME" in text


def test_readme_links_operator_setup_and_capture_agent_template() -> None:
    text = _read("README.md")

    assert "docs/operator-setup.md" in text
    assert "deploy/launchd/com.ambient-memory.capture-agent.plist" in text


def test_voiceprint_docs_exist_and_reference_live_enrollment() -> None:
    operator_text = _read("docs/operator-setup.md")
    script_text = _read("docs/ops/voiceprint-script.md")
    readme_text = _read("README.md")

    assert "voiceprint-live" in operator_text
    assert "quiet room" in script_text.lower()
    assert "ambient memory" in script_text.lower()
    assert "voiceprint-live" in readme_text
