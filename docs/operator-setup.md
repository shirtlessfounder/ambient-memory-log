# Operator Setup

Use this for each MacBook capture laptop.

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

## 1. Create `.env`

Start from the template:

```bash
cd "$HOME/Projects/ambient-memory-log"
cp .env.example .env
```

Set these values for the laptop:

- `SOURCE_ID` like `desk-a`
- `SOURCE_TYPE=macbook`
- `DEVICE_OWNER` like `dylan`
- `SPOOL_DIR` as an absolute path like `/Users/your-user/Projects/ambient-memory-log/spool/desk-a`
- `CAPTURE_DEVICE_NAME` from the exact device name reported by `ambient-memory list-devices`
- shared values: `DATABASE_URL`, `DATABASE_SSL_ROOT_CERT`, `AWS_REGION`, `S3_BUCKET`

## 2. Pick The Mic

List local devices:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory list-devices
```

Copy the exact built-in mic name into `CAPTURE_DEVICE_NAME` in `.env`.

## 3. Dry-Run The Agent

Validate config and device selection before loading the background service:

```bash
cd "$HOME/Projects/ambient-memory-log"
uv run ambient-memory agent run --dry-run --device "Built-in Microphone"
```

Expected result:

- command exits cleanly
- selected device is correct
- spool path is correct
- active window is correct

If the dry-run only works with a different device string, update `CAPTURE_DEVICE_NAME` in `.env` to match exactly.

## 4. Install The LaunchAgent

The generic plist already knows how to start the wrapper script. Do not edit the plist.

```bash
mkdir -p "$HOME/Library/LaunchAgents"
cp "$HOME/Projects/ambient-memory-log/deploy/launchd/com.ambient-memory.capture-agent.plist" \
  "$HOME/Library/LaunchAgents/com.ambient-memory.capture-agent.plist"
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.ambient-memory.capture-agent.plist"
launchctl kickstart -k "gui/$(id -u)/com.ambient-memory.capture-agent"
```

## 5. Stop Or Restart The Service

Stop:

```bash
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.ambient-memory.capture-agent.plist"
```

Restart after editing `.env`:

```bash
launchctl kickstart -k "gui/$(id -u)/com.ambient-memory.capture-agent"
```

## 6. Check Status

```bash
launchctl print "gui/$(id -u)/com.ambient-memory.capture-agent"
```

You should see the label `com.ambient-memory.capture-agent` and a running process.

## 7. Check Logs

Stdout:

```bash
tail -f /tmp/ambient-memory.capture-agent.stdout.log
```

Stderr:

```bash
tail -f /tmp/ambient-memory.capture-agent.stderr.log
```

## Common Mistakes

- repo not cloned at `$HOME/Projects/ambient-memory-log`
- `CAPTURE_DEVICE_NAME` does not exactly match `ambient-memory list-devices`
- `.env` missing or incomplete
- `SPOOL_DIR` is not an absolute path
- `ffmpeg` missing from the machine
- service loaded before the dry-run was validated
