# Ops Machine Setup

Use this on the central machine that processes uploaded audio.

Goal: run the worker and API continuously, and optionally capture the office mic on the same machine.

## What This Machine Does

This machine can play one or both roles:

- ops machine: runs the worker and API
- office mic machine: records the room microphone and uploads chunks

If the office mic is plugged into this same machine, it can do both.

## Prerequisites

- macOS
- repo cloned to `$HOME/Projects/ambient-memory-log`
- `uv` installed
- `ffmpeg` installed if this machine also captures the office mic

```bash
brew install ffmpeg
cd "$HOME/Projects/ambient-memory-log"
uv sync
```

## 1. Create `.env`

Create `.env` in the repo root:

```bash
cd "$HOME/Projects/ambient-memory-log"
cp .env.example .env
```

Set these values:

- `DATABASE_URL`
- `DATABASE_SSL_ROOT_CERT`
- `AWS_REGION`
- `S3_BUCKET`
- `DEEPGRAM_API_KEY`
- `PYANNOTE_API_KEY`

If this machine also captures the office mic, also set:

- `SOURCE_ID` like `room-1`
- `SOURCE_TYPE=room`
- `DEVICE_OWNER=conference-room`
- `SPOOL_DIR` as an absolute path like `/Users/your-user/Projects/ambient-memory-log/spool/room-1`
- `CAPTURE_DEVICE_NAME`

Optional:

- `API_HOST`
- `API_PORT`
- `API_PRESIGN_EXPIRES_IN`
- `CAPTURE_MAX_BACKLOG_FILES`

This doc assumes the shared database and bucket already exist.

## 2. Optional: Capture The Office Mic

If this machine owns the office microphone, validate the device first:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory list-devices
uv run ambient-memory agent run --dry-run --device "USB Audio CODEC"
```

Then load the capture agent:

```bash
mkdir -p "$HOME/Library/LaunchAgents"
cp "$HOME/Projects/ambient-memory-log/deploy/launchd/com.ambient-memory.capture-agent.plist" \
  "$HOME/Library/LaunchAgents/com.ambient-memory.capture-agent.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.ambient-memory.capture-agent.plist"
launchctl kickstart -k "gui/$(id -u)/com.ambient-memory.capture-agent"
```

If this machine does not own the office mic, skip this section.

## 3. Run The Worker

Manual start:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory worker run
```

What it does:

- reads uploaded chunks
- runs transcription
- runs speaker matching
- dedups across sources
- writes canonical utterances to Postgres

## 4. Run The API

Manual start:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory api --host 127.0.0.1 --port 8000
```

What it does:

- serves `/search`
- serves `/utterances/{utterance_id}`
- returns replay URLs for underlying audio chunks

## 5. Run Them Under launchd

The repo includes templates for long-running services:

- `deploy/launchd/com.ambient-memory.worker.plist`
- `deploy/launchd/com.ambient-memory.api.plist`

Before loading them, replace the placeholder paths and environment values inside each plist.

Then copy them into `~/Library/LaunchAgents/` and load them with `launchctl bootstrap`.

## 6. Check Logs

If you run the worker and API manually, watch the terminal output.

If you run them under launchd, use the log paths configured in the plist files.

For office-mic capture on this machine, the capture logs are:

```bash
tail -f /tmp/ambient-memory.capture-agent.stdout.log
tail -f /tmp/ambient-memory.capture-agent.stderr.log
```

## 7. Smoke Test

After the services are up, run the end-to-end validation in:

- `docs/ops/smoke-test.md`

That verifies capture, upload, worker processing, search, and replay.
