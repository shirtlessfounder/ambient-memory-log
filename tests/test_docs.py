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


def test_teammate_setup_doc_mentions_env_launchctl_and_logs() -> None:
    text = _read("docs/teammate-setup.md").lower()

    assert ".env.teammate" in text
    assert "capture_device_name" in text
    assert "launchctl" in text
    assert "log" in text


def test_teammate_setup_doc_covers_install_validation_voiceprint_and_service_flow() -> None:
    text = _read("docs/teammate-setup.md")

    assert "uv sync" in text
    assert "ffmpeg" in text
    assert "ambient-memory list-devices" in text
    assert "ambient-memory start-teammate --dry-run" in text
    assert "voiceprint-live" in text
    assert "launchctl bootstrap" in text
    assert "launchctl bootout" in text


def test_ops_machine_setup_doc_covers_worker_api_and_optional_room_capture() -> None:
    text = _read("docs/ops-machine-setup.md").lower()

    assert ".env.worker" in text
    assert ".env.api" in text
    assert "start-worker" in text
    assert "start-api" in text
    assert "room" in text
    assert "launchctl" in text
    assert "smoke-test.md" in text


def test_capture_agent_launchd_template_references_wrapper_script() -> None:
    text = _read("deploy/launchd/com.ambient-memory.capture-agent.plist")
    script_text = _read("scripts/start-capture-agent.sh")

    assert "start-capture-agent.sh" in text
    assert "<key>RunAtLoad</key>" in text
    assert "<key>KeepAlive</key>" in text
    assert ".env.teammate" in script_text
    assert "start-teammate" in script_text


def test_dual_capture_wrapper_script_checks_both_env_files_and_starts_cli() -> None:
    script_text = _read("scripts/start-dual-capture.sh")

    assert ".env.teammate" in script_text
    assert ".env.room-mic" in script_text
    assert "start-dual-capture" in script_text


def test_dual_capture_launchd_template_references_wrapper_script() -> None:
    text = _read("deploy/launchd/com.ambient-memory.dual-capture.plist")

    assert "start-dual-capture.sh" in text
    assert "<key>RunAtLoad</key>" in text
    assert "<key>KeepAlive</key>" in text
    assert "<key>StandardOutPath</key>" in text
    assert "<key>StandardErrorPath</key>" in text


def test_env_example_mentions_capture_device_name() -> None:
    text = _read(".env.example")

    assert "CAPTURE_DEVICE_NAME" in text


def test_env_example_mentions_silence_filter_settings() -> None:
    text = _read(".env.example")

    assert "SILENCE_FILTER_ENABLED" in text
    assert "SILENCE_MAX_VOLUME_DB" in text


def test_readme_links_teammate_and_ops_machine_setup_docs() -> None:
    text = _read("README.md")

    assert "docs/teammate-setup.md" in text
    assert "docs/ops-machine-setup.md" in text
    assert "deploy/launchd/com.ambient-memory.capture-agent.plist" in text
    assert "deploy/launchd/com.ambient-memory.dual-capture.plist" in text
    assert "start-teammate" in text
    assert "start-room-mic" in text
    assert "start-dual-capture" in text
    assert "start-worker" in text
    assert "start-api" in text


def test_readme_explains_launchd_background_startup() -> None:
    text = _read("README.md").lower()

    assert "launchd" in text
    assert "background" in text
    assert "terminal" in text
    assert "login" in text


def test_ops_machine_setup_doc_covers_dual_capture_mode() -> None:
    text = _read("docs/ops-machine-setup.md").lower()

    assert "dual capture" in text
    assert "start-dual-capture" in text
    assert "com.ambient-memory.dual-capture.plist" in text


def test_voiceprint_docs_exist_and_reference_live_enrollment() -> None:
    teammate_text = _read("docs/teammate-setup.md")
    script_text = _read("docs/ops/voiceprint-script.md")
    readme_text = _read("README.md")

    assert "voiceprint-live" in teammate_text
    assert "quiet room" in script_text.lower()
    assert "ambient memory" in script_text.lower()
    assert "voiceprint-live" in readme_text


def test_teammate_setup_doc_links_related_docs() -> None:
    text = _read("docs/teammate-setup.md")

    assert "docs/ops-machine-setup.md" in text
    assert "docs/ops/voiceprint-script.md" in text
    assert "docs/ops/smoke-test.md" in text


def test_teammate_setup_doc_mentions_conservative_silence_filter_behavior() -> None:
    text = _read("docs/teammate-setup.md").lower()

    assert "silent chunk" in text
    assert "conservative" in text
    assert "quiet speech" in text


def test_teammate_setup_doc_explains_one_time_launchd_setup() -> None:
    text = _read("docs/teammate-setup.md").lower()

    assert "one-time" in text
    assert "terminal" in text
    assert "login" in text


def test_ops_machine_setup_doc_mentions_conservative_silence_filter_behavior() -> None:
    text = _read("docs/ops-machine-setup.md").lower()

    assert "silent chunk" in text
    assert "conservative" in text
    assert "quiet speech" in text


def test_ops_machine_setup_doc_explains_launchd_as_background_service_flow() -> None:
    text = _read("docs/ops-machine-setup.md").lower()

    assert "one-time" in text
    assert "launchd" in text
    assert "terminal" in text
