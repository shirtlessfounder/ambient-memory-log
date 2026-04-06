# Teammate Setup

Use this on each teammate MacBook.

Goal: get one laptop recording, chunking, and uploading in the background with the right mic and voiceprint.

This doc is only for teammate laptops.

Daily vibe: this is a one-time setup. After you load the `launchd` service, macOS starts capture automatically on login and keeps it running in the background. You do not need to keep Terminal open or run a command every morning.

It does not cover:

- worker startup
- API startup
- database administration

Those live in `docs/ops-machine-setup.md`.

## Prerequisites

- macOS
- repo cloned to `$HOME/Projects/ambient-memory-log`
- `uv` installed
- `ffmpeg` installed

```bash
brew install ffmpeg
cd "$HOME/Projects/ambient-memory-log"
uv sync
```

## 1. Create `.env.teammate`

Create `.env.teammate` in the repo root:

```bash
cd "$HOME/Projects/ambient-memory-log"
cp .env.example .env.teammate
```

Set these shared values:

- `DATABASE_URL`
- `DATABASE_SSL_ROOT_CERT`
- `AWS_REGION`
- `S3_BUCKET`
- `DEEPGRAM_API_KEY`
- `PYANNOTE_API_KEY`

Set these laptop-specific values:

- `SOURCE_ID` like `desk-a`
- `SOURCE_TYPE=macbook`
- `DEVICE_OWNER` like `dylan`
- `SPOOL_DIR` as an absolute path like `/Users/your-user/Projects/ambient-memory-log/spool/desk-a`
- `CAPTURE_MAX_BACKLOG_FILES` if you need to override the default local backlog cap of `2048` chunks; this must be a positive integer
- `CAPTURE_DEVICE_NAME` from the exact device name reported by `ambient-memory list-devices`
- `SILENCE_FILTER_ENABLED=true` to keep the conservative local silence filter on
- `SILENCE_MAX_VOLUME_DB=-45.0` unless you need to tune it; lower, more negative values are safer for quiet speech

Optional shared value:

- `IMPORT_SPOOL_DIR` if you want prerecorded imports written somewhere other than `./spool/imports`

Teammates do not run the worker or API on their laptops.

The capture uploader uses a conservative local silence filter. Obviously silent chunks may be skipped locally before upload, but the default threshold is biased toward keeping quiet speech.

## 2. Pick The Mic

List local devices:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory list-devices
```

Copy the exact built-in mic name into `CAPTURE_DEVICE_NAME` in `.env.teammate`.

## 3. Dry-Run The Agent

Validate the config before starting background capture:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory start-teammate --dry-run
```

Expected result:

- command exits cleanly
- selected device is correct
- spool path is correct
- active window is correct

If the dry-run only works with a different device string, update `CAPTURE_DEVICE_NAME` in `.env.teammate` to match exactly.

## 4. Enroll Your Voiceprint

Each teammate should enroll one clean solo sample before you depend on named speaker matching.

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory enroll voiceprint-live --label "Dylan"
```

Notes:

- the command auto-picks the preferred local mic unless you pass `--device`
- it prints the exact script to read aloud
- you press `Enter` to start recording and `Enter` again to stop
- after each take, it lets you press `r` to re-record
- re-running it for the same name replaces the active voiceprint even if the case changes, so `Dylan` and `dylan` map to the same person
- saved samples go under `voiceprints/` and are ignored by git

If you want the exact script to read, see `docs/ops/voiceprint-script.md`.

## 5. Start Recording In Background

Load the background capture service once:

```bash
mkdir -p "$HOME/Library/LaunchAgents"
cp "$HOME/Projects/ambient-memory-log/deploy/launchd/com.ambient-memory.capture-agent.plist" \
  "$HOME/Library/LaunchAgents/com.ambient-memory.capture-agent.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.ambient-memory.capture-agent.plist"
launchctl kickstart -k "gui/$(id -u)/com.ambient-memory.capture-agent"
```

After that, recording should start automatically on login.

## 6. Stop Or Restart The Service

Stop:

```bash
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.ambient-memory.capture-agent.plist"
```

If you started teammate capture manually in a terminal instead of `launchd`, stop it with `Ctrl-C` in that terminal.

Restart after editing `.env`:

```bash
launchctl kickstart -k "gui/$(id -u)/com.ambient-memory.capture-agent"
```

## 7. Check Status

```bash
launchctl print "gui/$(id -u)/com.ambient-memory.capture-agent"
```

You should see the label `com.ambient-memory.capture-agent` and a running process.

## 8. Check Logs

Stdout:

```bash
tail -f /tmp/ambient-memory.capture-agent.stdout.log
```

Stderr:

```bash
tail -f /tmp/ambient-memory.capture-agent.stderr.log
```

If the laptop stays offline long enough to hit the local backlog cap, the agent pauses capture, keeps retrying backlog uploads, and resumes capture automatically once the backlog drains below the cap.

When the silence filter skips a silent chunk, the capture logs include the source id, filename, measured level, and threshold. If you suspect quiet speech is being missed, lower `SILENCE_MAX_VOLUME_DB` to a more negative value or disable the filter.

## Common Mistakes

- repo not cloned at `$HOME/Projects/ambient-memory-log`
- `CAPTURE_DEVICE_NAME` does not exactly match `ambient-memory list-devices`
- `.env.teammate` missing or incomplete
- `CAPTURE_MAX_BACKLOG_FILES` set too low for the expected offline window
- `SPOOL_DIR` is not an absolute path
- `ffmpeg` missing from the machine
- service loaded before the dry-run was validated

## Related Docs

- `docs/ops-machine-setup.md`
- `docs/ops/voiceprint-script.md`
- `docs/ops/smoke-test.md`
