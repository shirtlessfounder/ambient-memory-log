# Ambient Memory Smoke Test

Goal: prove one source can capture audio, upload a chunk, process it, surface it through `/search`, return a replay URL, and, when room capture is active, verify that `room-1` raw uploads stay immediate while searchable room output stays hidden until a named `AssemblyAI` batch is accepted.

## Before You Start

- Work from `/Users/your-user/Projects/ambient-memory-log`.
- Fill `.env` with the shared database, S3, and API keys from `.env.example`. If you are validating the room path, make sure the worker env also includes `DEEPGRAM_API_KEY`, `PYANNOTE_API_KEY`, `ASSEMBLYAI_API_KEY`, `ROOM_SPEAKER_ROSTER_PATH=./config/room-speakers.json`, `ROOM_ASSEMBLY_WINDOW_SECONDS=600`, and `ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS=120`.
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

Check the most recent `aa_audio_chunks` rows before you run the worker.

```bash
psql "$DATABASE_URL" \
  -c "select id, source_id, status, s3_key, started_at, uploaded_at from aa_audio_chunks order by uploaded_at desc limit 5;"
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

## 6. Optional: Verify Delayed `room-1` Publication

If room capture is running anywhere in the stack, validate the delayed room path directly. Keep the worker running under `launchd` or with `uv run ambient-memory start-worker` while you watch the database.

First confirm raw room uploads still land every `30s`:

```bash
psql "$DATABASE_URL" \
  -c "select source_id, status, left(s3_key, 80) as s3_key, started_at, uploaded_at from aa_audio_chunks where source_id = 'room-1' order by uploaded_at desc limit 10;"
```

Expected result:

- the newest rows show `source_id='room-1'`
- new rows keep arriving roughly every `30s` while room capture is active
- raw upload timing is unchanged even though room publication is delayed

Immediately after a fresh room upload, confirm room output does not appear right away:

```bash
psql "$DATABASE_URL" \
  -c "select source_id, vendor, left(text, 80) as text, speaker_hint, started_at, created_at from aa_transcript_candidates where source_id = 'room-1' order by created_at desc limit 10;"

psql "$DATABASE_URL" \
  -c "select canonical_source_id, speaker_name, left(text, 80) as text, started_at, created_at from aa_canonical_utterances where canonical_source_id = 'room-1' order by created_at desc limit 10;"

psql "$DATABASE_URL" \
  -c "select count(*) as diarization_names from aa_canonical_utterances where canonical_source_id = 'room-1' and speaker_name in ('A', 'B', 'C');"
```

Expected result:

- a fresh `room-1` upload does not create immediate searchable room output
- room transcript/canonical rows can lag by roughly `10` minutes during continuous speech because the worker waits for a `ROOM_ASSEMBLY_WINDOW_SECONDS` batch
- if the room goes quiet, a shorter trailing span can flush after `ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS`

After about `10` minutes of contiguous room audio, or after an idle flush:

- either new `room-1` rows appear with `vendor='assemblyai'` and real roster names
- or room output is still hidden because naming quality was not good enough yet
- `speaker_name` must never surface as `A/B/C`; those diarization labels are suppressed as real names even if `speaker_hint` still shows them internally
- if rows do appear, compare them with `/search` and verify the surfaced room names are real roster names or blank, never diarization letters

## 7. Start The API And Query `/search`

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

## 8. Open A Presigned Replay URL

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
