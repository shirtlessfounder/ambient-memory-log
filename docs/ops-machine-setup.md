# Ops Machine Setup

Use this on the central machine that processes uploaded audio.

Goal: run the worker and API continuously, and optionally capture the office mic or dual-capture both local mics on the same machine.

Daily vibe: if you use `launchd`, this is also a one-time setup. Once the worker, API, and optional capture launch agents are loaded, macOS starts them on login and keeps them running in the background. You do not need to leave a Terminal window open every day unless you are debugging.

## What This Machine Does

This machine can play one or more roles:

- ops machine: runs the worker and API
- room-mic machine: records the room microphone and uploads chunks
- dual-capture machine: runs one command and one launchd service while supervising `start-teammate` and `start-room-mic` as separate child capture processes

Supported capture modes on this machine:

- teammate-only: use `docs/teammate-setup.md`
- room-mic-only: run `uv run ambient-memory start-room-mic`
- dual capture: run `uv run ambient-memory start-dual-capture`

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

## 1. Create Role Env Files

Create the env files you need in the repo root:

```bash
cd "$HOME/Projects/ambient-memory-log"
cp .env.example .env.worker
cp .env.example .env.api
```

Set these values in `.env.worker`:

- `DATABASE_URL`
- `DATABASE_SSL_ROOT_CERT`
- `AWS_REGION`
- `DEEPGRAM_API_KEY`
- `PYANNOTE_API_KEY`

Set these values in `.env.api`:

- `DATABASE_URL`
- `DATABASE_SSL_ROOT_CERT`
- `AWS_REGION`
- `API_HOST`
- `API_PORT`
- `API_PRESIGN_EXPIRES_IN`

If this machine also captures the office mic, also set:

```bash
cp .env.example .env.room-mic
```

Set these values in `.env.room-mic`:

- `SOURCE_ID` like `room-1`
- `SOURCE_TYPE=room`
- `DEVICE_OWNER=conference-room`
- `SPOOL_DIR` as an absolute path like `/Users/your-user/Projects/ambient-memory-log/spool/room-1`
- `CAPTURE_DEVICE_NAME`
- `DATABASE_URL`
- `DATABASE_SSL_ROOT_CERT`
- `AWS_REGION`
- `S3_BUCKET`

Optional:

- `API_HOST`
- `API_PORT`
- `API_PRESIGN_EXPIRES_IN`
- `CAPTURE_MAX_BACKLOG_FILES`
- `SILENCE_FILTER_ENABLED`
- `SILENCE_MAX_VOLUME_DB`

If this machine will run dual capture, also create:

```bash
cp .env.example .env.teammate
```

Set the teammate-specific values in `.env.teammate`, especially:

- `SOURCE_ID` like `desk-a`
- `SOURCE_TYPE=teammate`
- `DEVICE_OWNER` for the local operator
- `SPOOL_DIR` as an absolute path like `/Users/your-user/Projects/ambient-memory-log/spool/desk-a`
- `CAPTURE_DEVICE_NAME`
- `DATABASE_URL`
- `DATABASE_SSL_ROOT_CERT`
- `AWS_REGION`
- `S3_BUCKET`

For any capture role on this machine, the uploader uses a conservative local silence filter. Obviously silent chunks may be skipped locally before upload, and lower, more negative `SILENCE_MAX_VOLUME_DB` values are safer for quiet speech.

This doc assumes the shared database and bucket already exist.

## 2. Optional: Capture Audio On This Machine

Choose one capture mode:

- room-mic-only: use `.env.room-mic` and start only `start-room-mic`
- dual capture: use both `.env.teammate` and `.env.room-mic`, then start `start-dual-capture`

If this machine owns the office microphone, validate the devices first:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory list-devices
uv run ambient-memory start-room-mic --dry-run
```

If you are using dual capture, also validate the teammate mic:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory start-teammate --dry-run
```

Manual start commands:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory start-room-mic
uv run ambient-memory start-dual-capture
```

`start-dual-capture` is the approved one-command UX. It stays orchestration-only and supervises these two child processes under the hood:

- `uv run ambient-memory start-teammate`
- `uv run ambient-memory start-room-mic`

For always-on dual capture, load the dedicated launchd service:

```bash
mkdir -p "$HOME/Library/LaunchAgents"
cp "$HOME/Projects/ambient-memory-log/deploy/launchd/com.ambient-memory.dual-capture.plist" \
  "$HOME/Library/LaunchAgents/com.ambient-memory.dual-capture.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.ambient-memory.dual-capture.plist"
launchctl kickstart -k "gui/$(id -u)/com.ambient-memory.dual-capture"
```

If this machine only runs the room mic, keep using the direct `start-room-mic` command and skip the dual-capture launchd service.

## 3. Run The Worker

Manual start:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory start-worker
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
uv run ambient-memory start-api
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

For dual capture on this machine, the capture logs are:

```bash
tail -f /tmp/ambient-memory.dual-capture.stdout.log
tail -f /tmp/ambient-memory.dual-capture.stderr.log
```

If the silence filter skips a silent chunk, the log line includes the source id, filename, measured level, and threshold. If quiet speech appears to be missing, lower `SILENCE_MAX_VOLUME_DB` or disable the filter for that capture env file.

## 7. Smoke Test

After the services are up, run the end-to-end validation in:

- `docs/ops/smoke-test.md`

That verifies capture, upload, worker processing, search, and replay.
