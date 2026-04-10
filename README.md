# Ambient Memory Log

Recall-first ambient memory log MVP.

## Setup

1. Install `ffmpeg` on macOS.
2. Install project dependencies with `uv sync`.
3. Create the role-specific env file you need from `.env.example`.

`CAPTURE_MAX_BACKLOG_FILES` defaults to `2048` and must be a positive integer so the capture agent can tolerate a longer offline window before it pauses local capture and focuses on draining backlog uploads.

For normal day-to-day use, the intended setup is `launchd` on macOS. That means you do the setup once, then macOS keeps the service running in the background. Teammates should not need to keep Terminal open or re-run commands every morning. The service starts again on login.

Raw capture, S3 storage, canonical utterance storage, search, and replay stay the same on this branch. Inside the worker, `room-1` now uses `AssemblyAI` for transcript + room speaker labeling, while non-room sources still use `Deepgram` + `pyannote`.

For `room-1`, raw capture and upload still happen every `30s`. Publication is delayed on purpose: the worker batches contiguous room chunks into `ROOM_ASSEMBLY_WINDOW_SECONDS` windows (default `600`), can idle-flush a shorter trailing span after `ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS` (default `120`), and writes room transcript/search output only after `AssemblyAI` returns at least one real roster name from `ROOM_SPEAKER_ROSTER_PATH`. Bare diarization labels like `A/B/C` are treated as hints, not surfaced speaker names.

There are now two separate dead-air controls. First, capture can skip obviously silent `30s` chunks locally with the upload-time silence filter. Second, before `room-1` hits `AssemblyAI`, the worker measures speech-like activity on the stitched room window. If speech is below `ROOM_MIN_SPEECH_SECONDS` (default `20`), that room window is skipped permanently, never sent to `AssemblyAI`, and does not retry.

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
