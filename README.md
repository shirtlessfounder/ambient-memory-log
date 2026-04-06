# Ambient Memory Log

Recall-first ambient memory log MVP.

## Setup

1. Install `ffmpeg` on macOS.
2. Install project dependencies with `uv sync`.
3. Create the role-specific env file you need from `.env.example`.

`CAPTURE_MAX_BACKLOG_FILES` defaults to `2048` and must be a positive integer so the capture agent can tolerate a longer offline window before it pauses local capture and focuses on draining backlog uploads.

For normal day-to-day use, the intended setup is `launchd` on macOS. That means you do the setup once, then macOS keeps the service running in the background. Teammates should not need to keep Terminal open or re-run commands every morning. The service starts again on login.

## Commands

- `uv run ambient-memory list-devices`
- `uv run ambient-memory agent run --dry-run --device "Built-in Microphone"`
- `uv run ambient-memory start-teammate`
- `uv run ambient-memory start-room-mic`
- `uv run ambient-memory start-dual-capture`
- `uv run ambient-memory start-worker`
- `uv run ambient-memory start-api`
- `uv run ambient-memory import-recording ./meeting.m4a --start "2026-04-03 09:00"`
  Re-running the same source id now prompts before append; use `--allow-existing-source-id` only for deliberate reruns.
- `uv run ambient-memory worker run-once --dry-run`
- `uv run ambient-memory worker run --poll-seconds 5`
- `uv run ambient-memory api --host 127.0.0.1 --port 8000`
- `uv run ambient-memory enroll voiceprint --label "Dylan" --audio ./sample.wav`
- `uv run ambient-memory enroll voiceprint-live --label "Dylan"`

## Ops

- Teammate setup: `docs/teammate-setup.md`
- Ops machine setup: `docs/ops-machine-setup.md`
- Smoke test: `docs/ops/smoke-test.md`
- Launchd capture-agent template: `deploy/launchd/com.ambient-memory.capture-agent.plist`
- Launchd dual-capture template: `deploy/launchd/com.ambient-memory.dual-capture.plist`
- Launchd worker template: `deploy/launchd/com.ambient-memory.worker.plist`
- Launchd API template: `deploy/launchd/com.ambient-memory.api.plist`
