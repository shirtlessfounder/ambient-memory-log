from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_smoke_test_doc_mentions_capture_worker_api_and_search() -> None:
    text = _read("docs/ops/smoke-test.md")

    assert "agent run --dry-run" in text
    assert "worker run-once --dry-run" in text
    assert "vendor='assemblyai'" in text or 'vendor="assemblyai"' in text
    assert "/search" in text
    assert "presigned" in text.lower()


def test_smoke_test_doc_mentions_assemblyai_room_verification() -> None:
    text = _read("docs/ops/smoke-test.md").lower()

    assert "room-1" in text
    assert "assemblyai" in text
    assert "delayed" in text
    assert "10" in text
    assert "a/b/c" in text


def test_smoke_test_doc_covers_required_operator_flow() -> None:
    text = _read("docs/ops/smoke-test.md").lower()

    assert "macbook" in text
    assert "room-box" in text
    assert "live capture" in text
    assert "postgres" in text
    assert "open" in text and "replay" in text
    assert "30s" in text or "30 seconds" in text
    assert "hidden" in text


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
    assert "aws configure" in text
    assert "capture_device_name" in text
    assert "launchctl" in text
    assert "log" in text


def test_teammate_setup_doc_covers_install_validation_voiceprint_and_service_flow() -> None:
    text = _read("docs/teammate-setup.md")

    assert "uv sync" in text
    assert "ffmpeg" in text
    assert "awscli" in text.lower()
    assert "ambient-memory list-devices" in text
    assert "ambient-memory start-teammate --dry-run" in text
    assert "voiceprint-live" in text
    assert "launchctl bootstrap" in text
    assert "launchctl bootout" in text
    assert "ctrl-c" in text.lower()


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


def test_capture_launchd_templates_use_interactive_process_type_for_audio_capture() -> None:
    capture_text = _read("deploy/launchd/com.ambient-memory.capture-agent.plist")
    dual_text = _read("deploy/launchd/com.ambient-memory.dual-capture.plist")

    assert "<key>ProcessType</key>" in capture_text
    assert "<string>Interactive</string>" in capture_text
    assert "<key>ProcessType</key>" in dual_text
    assert "<string>Interactive</string>" in dual_text


def test_env_example_mentions_capture_device_name() -> None:
    text = _read(".env.example")

    assert "CAPTURE_DEVICE_NAME" in text


def test_env_example_mentions_assemblyai_api_key() -> None:
    text = _read(".env.example")

    assert "ASSEMBLYAI_API_KEY" in text


def test_env_example_mentions_openai_api_key_for_room_enrichment() -> None:
    text = _read(".env.example")

    assert "OPENAI_API_KEY" in text


def test_env_example_mentions_room_window_settings() -> None:
    text = _read(".env.example")

    assert "ROOM_SPEAKER_ROSTER_PATH" in text
    assert "ROOM_ASSEMBLY_WINDOW_SECONDS" in text
    assert "ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS" in text


def test_env_example_mentions_silence_filter_settings() -> None:
    text = _read(".env.example")

    assert "SILENCE_FILTER_ENABLED" in text
    assert "SILENCE_MAX_VOLUME_DB" in text


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


def test_ops_machine_setup_doc_mentions_room_window_speech_gate() -> None:
    text = _read("docs/ops-machine-setup.md").lower()

    assert "room_min_speech_seconds" in text
    assert "20" in text
    assert "skip" in text
    assert "assemblyai" in text
    assert "do not retry" in text or "does not retry" in text


def test_smoke_test_doc_mentions_room_window_speech_gate() -> None:
    text = _read("docs/ops/smoke-test.md").lower()

    assert "room_min_speech_seconds" in text
    assert "20" in text
    assert "skip" in text
    assert "assemblyai" in text


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


def test_readme_explains_room_1_assemblyai_path() -> None:
    text = _read("README.md").lower()

    assert "room-1" in text
    assert "assemblyai" in text
    assert "deepgram" in text
    assert "pyannote" in text
    assert "delayed" in text
    assert "room_speaker_roster_path" in text
    assert "a/b/c" in text


def test_readme_mentions_room_enrichment_command() -> None:
    text = _read("README.md").lower()

    assert "enrich-room" in text
    assert "--hours 4" in text
    assert "openai_api_key" in text


def test_ops_machine_setup_mentions_room_enrichment_command() -> None:
    text = _read("docs/ops-machine-setup.md").lower()

    assert "enrich-room" in text
    assert "openai_api_key" in text
    assert "4h" in text or "4 hours" in text


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
    assert "launchctl bootout" in text
    assert "ctrl-c" in text


def test_ops_machine_setup_doc_mentions_assemblyai_worker_env_and_room_path() -> None:
    text = _read("docs/ops-machine-setup.md").lower()

    assert "assemblyai_api_key" in text
    assert "room-1" in text
    assert "assemblyai" in text
    assert "deepgram" in text
    assert "pyannote" in text
    assert "room_speaker_roster_path" in text
    assert "room_assembly_window_seconds" in text
    assert "room_assembly_idle_flush_seconds" in text
    assert "a/b/c" in text


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


def test_teammate_setup_doc_mentions_local_aws_credentials_for_s3_uploads() -> None:
    text = _read("docs/teammate-setup.md").lower()

    assert "s3" in text
    assert "aws configure" in text
    assert "unable to locate credentials" in text
    assert "retry/" in text


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


def test_capture_docs_explain_interactive_launchd_requirement_for_mic_capture() -> None:
    teammate_text = _read("docs/teammate-setup.md").lower()
    ops_text = _read("docs/ops-machine-setup.md").lower()

    assert "interactive" in teammate_text
    assert "interactive" in ops_text
    assert "mic capture" in teammate_text
    assert "mic capture" in ops_text
