# Ambient Memory Smoke Test

Goal: prove one source can capture audio, upload a chunk, process it, surface it through `/search`, and return a replay URL before you roll the stack wider.

## Before You Start

- Work from `/Users/your-user/Projects/ambient-memory-log`.
- Fill `.env` with the shared database, S3, and API keys from `.env.example`.
- Install `ffmpeg`, `jq`, and `psql`.
- Pick a searchable phrase for the live capture, such as `ambient smoke test alpha`.

## 1. Dry-Run One MacBook Agent

Use a MacBook source first so you can verify the operator laptop has the right microphone and source metadata wired up.

```bash
export SOURCE_ID=desk-a
export SOURCE_TYPE=macbook
export DEVICE_OWNER=dylan
export SPOOL_DIR="$PWD/spool/desk-a"

uv run ambient-memory agent run --dry-run --device "Built-in Microphone"
```

Expected result: the command exits cleanly after printing the resolved capture configuration. No audio files should be written and no upload should start.

## 2. Dry-Run One Room-Box Agent

Validate the room-box profile separately before you leave it running unattended.

```bash
export SOURCE_ID=room-1
export SOURCE_TYPE=room
export DEVICE_OWNER=conference-room
export SPOOL_DIR="$PWD/spool/room-1"

uv run ambient-memory agent run --dry-run --device "USB Audio CODEC"
```

Expected result: the dry-run confirms the room-box device selection and source metadata without touching S3 or Postgres.

## 3. Capture One Live Chunk

Switch back to the MacBook source, speak the test phrase twice, then stop the agent after the first chunk uploads.

```bash
export SOURCE_ID=desk-a
export SOURCE_TYPE=macbook
export DEVICE_OWNER=dylan
export SPOOL_DIR="$PWD/spool/desk-a"

uv run ambient-memory agent run --device "Built-in Microphone"
```

Operator notes:

- Let it run for at least 35 seconds so one 30-second chunk can close and upload.
- Say `ambient smoke test alpha` clearly during the live capture.
- Stop the process with `Ctrl-C` after you see the first upload complete.

## 4. Confirm The Chunk Upload Row In Postgres

Check the most recent `audio_chunks` rows before you run the worker.

```bash
psql "$DATABASE_URL" \
  -c "select id, source_id, status, s3_key, started_at, uploaded_at from audio_chunks order by uploaded_at desc limit 5;"
```

Expected result: the newest row matches `SOURCE_ID=desk-a`, the `s3_key` points at the new chunk, and `status` is `uploaded`.

## 5. Preview And Run The Worker Once

Use the dry-run first so you know the worker sees the pending upload, then run the real pass.

```bash
uv run ambient-memory worker run-once --dry-run
uv run ambient-memory worker run-once
```

Expected result:

- the dry-run reports at least one pending uploaded chunk
- the real run reports processed chunks and zero failures

## 6. Start The API And Query `/search`

Start the API in a second terminal if it is not already running under launchd.

```bash
uv run ambient-memory api --host 127.0.0.1 --port 8000
```

Then query the phrase you spoke during capture.

```bash
curl --silent --get "http://127.0.0.1:8000/search" \
  --data-urlencode "q=ambient smoke test alpha" | jq
```

Expected result: the response includes at least one item whose `text` contains the phrase and whose `provenance_summary` references the captured source.

## 7. Open A Presigned Replay URL

Grab the first utterance id from `/search`, fetch its detail record, then open the first replay URL in the macOS default browser or audio player.

```bash
UTTERANCE_ID=$(
  curl --silent --get "http://127.0.0.1:8000/search" \
    --data-urlencode "q=ambient smoke test alpha" |
    jq -r '.items[0].id'
)

REPLAY_URL=$(
  curl --silent "http://127.0.0.1:8000/utterances/${UTTERANCE_ID}" |
    jq -r '.replay_audio[0].url'
)

open "$REPLAY_URL"
```

Expected result: the presigned replay URL opens successfully and the audio matches the phrase you spoke during capture.

## Launchd Templates

Use these as starting points for long-running services after the smoke test passes:

- `deploy/launchd/com.ambient-memory.worker.plist`
- `deploy/launchd/com.ambient-memory.api.plist`
