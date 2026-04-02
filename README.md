# Ambient Memory Log

Recall-first ambient memory log MVP.

## Setup

1. Install `ffmpeg` on macOS.
2. Install project dependencies with `uv sync`.
3. Copy `.env.example` to `.env` and fill in the required values.
4. Apply the schema with `uv run alembic upgrade head`.

## Commands

- `uv run ambient-memory list-devices`
- `uv run ambient-memory agent run --dry-run --device "Built-in Microphone"`
- `uv run ambient-memory worker run-once --dry-run`
- `uv run ambient-memory worker run --poll-seconds 5`
- `uv run ambient-memory api --host 127.0.0.1 --port 8000`
- `uv run ambient-memory enroll voiceprint --label "Dylan" --audio ./sample.wav`

## Ops

- Smoke test: `docs/ops/smoke-test.md`
- Launchd worker template: `deploy/launchd/com.ambient-memory.worker.plist`
- Launchd API template: `deploy/launchd/com.ambient-memory.api.plist`
