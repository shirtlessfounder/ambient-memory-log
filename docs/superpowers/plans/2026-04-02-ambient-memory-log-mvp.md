# Ambient Memory Log MVP Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a recall-first MVP that records ambient speech from 4 MacBooks plus one room source, stores raw audio in S3, writes canonical searchable transcripts to RDS Postgres, and preserves replay/reprocessing paths.

**Architecture:** One Python repo contains a reusable macOS capture agent, a single pipeline worker, and a minimal read API. Capture agents roll 30-second chunks to S3 and register chunk metadata in Postgres; the worker transcribes with Deepgram, identifies speakers with pyannote voiceprints, merges duplicates, and writes canonical utterances into Postgres.

**Tech Stack:** Python 3.12, `uv`, `pytest`, FastAPI, SQLAlchemy, Alembic, `boto3`, `httpx`, `ffmpeg` (`avfoundation` on macOS), PostgreSQL full-text search, Deepgram API, pyannoteAI API

---

## File Structure

### Root

- `pyproject.toml`
  - Python package metadata, dependencies, pytest config, CLI entrypoint
- `.gitignore`
  - Python, local audio spool, env file, macOS noise
- `.env.example`
  - safe template for required environment variables
- `README.md`
  - local setup, command reference, architecture summary
- `alembic.ini`
  - migration config

### Docs and Ops

- `docs/ops/macos-agent-setup.md`
  - mic permissions, ffmpeg install, launchd install steps for each MacBook
- `docs/ops/room-box-setup.md`
  - central source hardware placement and setup
- `docs/ops/smoke-test.md`
  - end-to-end manual verification checklist
- `deploy/launchd/com.ambient-memory.agent.plist`
  - template launchd unit for local agents
- `deploy/launchd/com.ambient-memory.worker.plist`
  - template launchd unit for the worker
- `deploy/launchd/com.ambient-memory.api.plist`
  - template launchd unit for the read API

### App Package

- `src/ambient_memory/__init__.py`
  - package marker
- `src/ambient_memory/cli.py`
  - `ambient-memory` CLI entrypoints
- `src/ambient_memory/config.py`
  - environment parsing and typed settings
- `src/ambient_memory/logging.py`
  - structured logging setup
- `src/ambient_memory/db.py`
  - SQLAlchemy engine, session factory, health helpers
- `src/ambient_memory/models.py`
  - ORM models and schema constants

### Capture

- `src/ambient_memory/capture/device_discovery.py`
  - enumerate `ffmpeg` avfoundation devices and validate selected input
- `src/ambient_memory/capture/ffmpeg.py`
  - build and supervise `ffmpeg` capture commands
- `src/ambient_memory/capture/spool.py`
  - local spool manifest and bounded retry queue
- `src/ambient_memory/capture/uploader.py`
  - S3 upload and chunk registration in Postgres
- `src/ambient_memory/capture/agent.py`
  - main capture loop, schedule gating, heartbeats

### Integrations

- `src/ambient_memory/integrations/s3_store.py`
  - upload/download/presign helpers for S3
- `src/ambient_memory/integrations/deepgram_client.py`
  - Deepgram pre-recorded transcription wrapper
- `src/ambient_memory/integrations/pyannote_client.py`
  - voiceprint enrollment and identification wrapper

### Pipeline

- `src/ambient_memory/pipeline/windows.py`
  - group chunk rows into processing windows
- `src/ambient_memory/pipeline/normalize.py`
  - map vendor payloads to internal segment records
- `src/ambient_memory/pipeline/speaker_matching.py`
  - combine pyannote matches with local-device priors
- `src/ambient_memory/pipeline/dedup.py`
  - canonical utterance merge logic
- `src/ambient_memory/pipeline/worker.py`
  - poll pending chunks, run the full pipeline, persist results

### Read API

- `src/ambient_memory/api/schemas.py`
  - request/response DTOs
- `src/ambient_memory/api/search.py`
  - Postgres search queries and replay lookup
- `src/ambient_memory/api/app.py`
  - FastAPI app factory and routes

### Migrations

- `migrations/env.py`
  - Alembic runtime
- `migrations/versions/20260402_0001_initial_schema.py`
  - initial schema with search indexes

### Tests

- `tests/test_config.py`
  - config/env parsing tests
- `tests/test_cli.py`
  - CLI wiring tests
- `tests/test_docs.py`
  - docs and smoke-checklist structure tests
- `tests/db/test_models.py`
  - model metadata and search column tests
- `tests/capture/test_device_discovery.py`
  - avfoundation parsing tests
- `tests/capture/test_ffmpeg.py`
  - ffmpeg command builder tests
- `tests/capture/test_spool.py`
  - spool retry and manifest tests
- `tests/capture/test_uploader.py`
  - S3 registration flow tests
- `tests/pipeline/test_windows.py`
  - chunk window grouping tests
- `tests/pipeline/test_normalize.py`
  - vendor-payload normalization tests
- `tests/pipeline/test_speaker_matching.py`
  - pyannote/device-prior scoring tests
- `tests/pipeline/test_dedup.py`
  - transcript merge and provenance tests
- `tests/pipeline/test_worker.py`
  - end-to-end worker orchestration tests with mocks
- `tests/api/test_search.py`
  - search and replay response tests

## Chunk 1: Foundations

### Task 1: Bootstrap the Python Project

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `src/ambient_memory/__init__.py`
- Create: `src/ambient_memory/cli.py`
- Create: `src/ambient_memory/config.py`
- Create: `src/ambient_memory/logging.py`
- Test: `tests/test_config.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing config and CLI tests**

```python
def test_settings_require_database_and_bucket():
    with pytest.raises(ValidationError):
        Settings.model_validate({})

def test_cli_lists_expected_commands():
    result = runner.invoke(app, ["--help"])
    assert "agent" in result.output
    assert "worker" in result.output
    assert "api" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -q`
Expected: `ModuleNotFoundError` or missing `Settings` / CLI app symbols

- [ ] **Step 3: Create the project scaffold and minimal config/CLI implementation**

Include:
- `Settings` with safe env names for `DATABASE_URL`, `DATABASE_SSL_ROOT_CERT`, `AWS_REGION`, `S3_BUCKET`, `DEEPGRAM_API_KEY`, `PYANNOTE_API_KEY`, `SOURCE_ID`, `SOURCE_TYPE`, `DEVICE_OWNER`, `SPOOL_DIR`, `ACTIVE_START_LOCAL`, `ACTIVE_END_LOCAL`
- CLI subcommands: `agent`, `worker`, `api`, `enroll`, `list-devices`
- `README.md` with `uv sync`, `ffmpeg` dependency note, and command overview

- [ ] **Step 4: Re-run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore .env.example README.md src/ambient_memory/__init__.py src/ambient_memory/cli.py src/ambient_memory/config.py src/ambient_memory/logging.py tests/test_config.py tests/test_cli.py
git commit -m "build: bootstrap ambient memory python project"
```

### Task 2: Add Database Models and Initial Migration

**Files:**
- Create: `alembic.ini`
- Create: `src/ambient_memory/db.py`
- Create: `src/ambient_memory/models.py`
- Create: `migrations/env.py`
- Create: `migrations/versions/20260402_0001_initial_schema.py`
- Test: `tests/db/test_models.py`

- [ ] **Step 1: Write failing model tests for the MVP schema**

```python
def test_models_expose_expected_tables():
    assert {"sources", "audio_chunks", "voiceprints", "transcript_candidates", "canonical_utterances", "utterance_sources", "agent_heartbeats"} <= set(Base.metadata.tables)

def test_canonical_utterances_has_search_index():
    table = Base.metadata.tables["canonical_utterances"]
    assert "search_vector" in table.c
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/db/test_models.py -q`
Expected: missing `Base` or missing tables

- [ ] **Step 3: Implement SQLAlchemy models and Alembic migration**

Schema requirements:
- `sources`: capture source registry
- `audio_chunks`: one row per uploaded chunk, with upload/transcription status
- `voiceprints`: stored pyannote voiceprints for the 4 enrolled users
- `transcript_candidates`: normalized per-source transcript segments
- `canonical_utterances`: merged searchable transcript rows
- `utterance_sources`: provenance links from canonical utterances to candidate segments
- `agent_heartbeats`: last-seen and last-upload timestamps per source

Search requirements:
- `canonical_utterances.search_vector` column
- GIN index over `search_vector`
- ordinary indexes on `audio_chunks(status, started_at)` and `canonical_utterances(started_at)`

- [ ] **Step 4: Re-run tests and smoke the migration**

Run: `uv run pytest tests/db/test_models.py -q`
Expected: model tests pass

Run: `uv run alembic upgrade head`
Expected: migration applies with no errors against the configured database

- [ ] **Step 5: Commit**

```bash
git add alembic.ini src/ambient_memory/db.py src/ambient_memory/models.py migrations/env.py migrations/versions/20260402_0001_initial_schema.py tests/db/test_models.py
git commit -m "feat: add ambient memory database schema"
```

### Task 3: Add Shared S3 and Repository Primitives

**Files:**
- Create: `src/ambient_memory/integrations/s3_store.py`
- Create: `tests/capture/test_uploader.py`
- Modify: `src/ambient_memory/db.py`
- Modify: `src/ambient_memory/models.py`

- [ ] **Step 1: Write failing tests for S3 object key generation and chunk registration**

```python
def test_chunk_key_includes_source_and_timestamp():
    key = build_chunk_key("desk-a", datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC))
    assert key.startswith("raw-audio/desk-a/2026/04/02/")

def test_register_chunk_marks_row_pending_transcription(session):
    row = register_uploaded_chunk(...)
    assert row.status == "uploaded"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/capture/test_uploader.py -q`
Expected: missing helper functions or failing assertions

- [ ] **Step 3: Implement S3 helper and chunk registration path**

Include:
- deterministic object key layout
- upload helper
- presigned URL helper for replay
- DB helper to create/update `audio_chunks` rows after upload

- [ ] **Step 4: Re-run tests to verify they pass**

Run: `uv run pytest tests/capture/test_uploader.py -q`
Expected: uploader tests pass

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/integrations/s3_store.py src/ambient_memory/db.py src/ambient_memory/models.py tests/capture/test_uploader.py
git commit -m "feat: add chunk storage primitives"
```

## Chunk 2: Capture Agents

### Task 4: Implement Device Discovery and FFmpeg Command Builder

**Files:**
- Create: `src/ambient_memory/capture/device_discovery.py`
- Create: `src/ambient_memory/capture/ffmpeg.py`
- Test: `tests/capture/test_device_discovery.py`
- Test: `tests/capture/test_ffmpeg.py`

- [ ] **Step 1: Write failing tests for avfoundation parsing and ffmpeg command generation**

```python
def test_parse_avfoundation_devices_extracts_audio_inputs():
    devices = parse_avfoundation_list(SAMPLE_OUTPUT)
    assert devices[0].name == "MacBook Pro Microphone"

def test_build_capture_command_sets_segment_duration():
    cmd = build_capture_command(...)
    assert "-f" in cmd and "segment" in cmd
    assert "30" in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/capture/test_device_discovery.py tests/capture/test_ffmpeg.py -q`
Expected: missing parser/command builder

- [ ] **Step 3: Implement device listing and command builder**

Requirements:
- support `ffmpeg -f avfoundation -list_devices true -i ""`
- select a single configured audio input
- output rolling 30-second chunks
- keep audio format consistent for downstream STT
- write chunks into the local spool directory

- [ ] **Step 4: Re-run tests to verify they pass**

Run: `uv run pytest tests/capture/test_device_discovery.py tests/capture/test_ffmpeg.py -q`
Expected: capture tests pass

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/capture/device_discovery.py src/ambient_memory/capture/ffmpeg.py tests/capture/test_device_discovery.py tests/capture/test_ffmpeg.py
git commit -m "feat: add macos capture device support"
```

### Task 5: Build the Spool, Upload, and Heartbeat Loop

**Files:**
- Create: `src/ambient_memory/capture/spool.py`
- Create: `src/ambient_memory/capture/uploader.py`
- Create: `src/ambient_memory/capture/agent.py`
- Test: `tests/capture/test_spool.py`
- Test: `tests/capture/test_uploader.py`
- Modify: `src/ambient_memory/cli.py`

- [ ] **Step 1: Write failing tests for backlog retry and heartbeat behavior**

```python
def test_failed_upload_stays_in_spool_until_retry():
    spool.enqueue(chunk)
    uploader.upload_next()
    assert spool.pending_count() == 1

def test_agent_heartbeat_updates_last_seen(session):
    agent.write_heartbeat()
    assert session.get(AgentHeartbeat, "desk-a").last_seen_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/capture/test_spool.py tests/capture/test_uploader.py -q`
Expected: missing agent/spool implementations

- [ ] **Step 3: Implement the capture agent loop**

Requirements:
- run only inside the configured active window
- continuously capture chunks during active hours
- upload finished chunks to S3
- register uploaded chunks in Postgres
- retry failed uploads from a bounded local backlog
- update `agent_heartbeats`
- expose `ambient-memory agent run`

- [ ] **Step 4: Re-run tests and perform a local dry run**

Run: `uv run pytest tests/capture/test_spool.py tests/capture/test_uploader.py -q`
Expected: tests pass

Run: `uv run ambient-memory list-devices`
Expected: at least one audio input listed on a configured Mac

Run: `uv run ambient-memory agent run --dry-run`
Expected: logs show the chosen device, spool path, and active window without recording audio

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/capture/spool.py src/ambient_memory/capture/uploader.py src/ambient_memory/capture/agent.py src/ambient_memory/cli.py tests/capture/test_spool.py tests/capture/test_uploader.py
git commit -m "feat: add capture agent and upload loop"
```

### Task 6: Add Room-Box and Mac Setup Artifacts

**Files:**
- Create: `docs/ops/macos-agent-setup.md`
- Create: `docs/ops/room-box-setup.md`
- Create: `deploy/launchd/com.ambient-memory.agent.plist`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Extend the CLI help test to cover install-facing commands and docs references**

```python
def test_cli_help_mentions_list_devices_command():
    result = runner.invoke(app, ["list-devices", "--help"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run the targeted test**

Run: `uv run pytest tests/test_cli.py -q`
Expected: fail until CLI/docs are updated

- [ ] **Step 3: Write the setup docs and launchd template**

Include:
- microphone permission steps
- `ffmpeg` install command
- env file placement
- launchd load/unload commands
- room-box mic placement guidance for the whiteboard and roam zone

- [ ] **Step 4: Re-run the test and manually validate the docs**

Run: `uv run pytest tests/test_cli.py -q`
Expected: tests pass

Manual check: read both setup docs end-to-end and confirm a new engineer could configure one MacBook agent and one room box without asking for missing steps

- [ ] **Step 5: Commit**

```bash
git add docs/ops/macos-agent-setup.md docs/ops/room-box-setup.md deploy/launchd/com.ambient-memory.agent.plist tests/test_cli.py
git commit -m "docs: add agent and room box setup guides"
```

## Chunk 3: Processing Pipeline

### Task 7: Add Deepgram Transcription and Segment Normalization

**Files:**
- Create: `src/ambient_memory/integrations/deepgram_client.py`
- Create: `src/ambient_memory/pipeline/normalize.py`
- Test: `tests/pipeline/test_normalize.py`
- Modify: `src/ambient_memory/models.py`

- [ ] **Step 1: Write failing tests for Deepgram response normalization**

```python
def test_normalize_deepgram_response_produces_segments():
    segments = normalize_deepgram_response(SAMPLE_DEEPGRAM_PAYLOAD, source_id="desk-a")
    assert segments[0].text == "hello there"
    assert segments[0].source_id == "desk-a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_normalize.py -q`
Expected: missing client/normalizer

- [ ] **Step 3: Implement the Deepgram wrapper and normalizer**

Requirements:
- submit pre-recorded audio from S3 or downloaded local bytes
- request timestamps and diarization fields
- normalize vendor payloads into internal segment DTOs
- keep the raw vendor payload for debugging/provenance

- [ ] **Step 4: Re-run tests to verify they pass**

Run: `uv run pytest tests/pipeline/test_normalize.py -q`
Expected: normalization tests pass

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/integrations/deepgram_client.py src/ambient_memory/pipeline/normalize.py src/ambient_memory/models.py tests/pipeline/test_normalize.py
git commit -m "feat: add deepgram transcription integration"
```

### Task 8: Add Voice Enrollment and Speaker Matching

**Files:**
- Create: `src/ambient_memory/integrations/pyannote_client.py`
- Create: `src/ambient_memory/pipeline/speaker_matching.py`
- Test: `tests/pipeline/test_speaker_matching.py`
- Modify: `src/ambient_memory/cli.py`

- [ ] **Step 1: Write failing tests for device-owner priors and pyannote matches**

```python
def test_local_source_biases_to_device_owner_when_confident():
    match = choose_speaker(source_owner="dylan", pyannote_match="dylan", confidence=81)
    assert match.speaker_name == "dylan"

def test_low_confidence_room_segment_remains_uncertain():
    match = choose_speaker(source_owner=None, pyannote_match="dylan", confidence=32)
    assert match.speaker_name is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_speaker_matching.py -q`
Expected: missing speaker matcher

- [ ] **Step 3: Implement pyannote client and enrollment flow**

Requirements:
- `ambient-memory enroll voiceprint --label <name> --audio <path>`
- persist reusable voiceprints in `voiceprints`
- identify speakers for a processing window
- combine pyannote confidence with local-source priors
- leave uncertain matches unnamed instead of forcing a guess

- [ ] **Step 4: Re-run tests and smoke the enrollment CLI**

Run: `uv run pytest tests/pipeline/test_speaker_matching.py -q`
Expected: tests pass

Run: `uv run ambient-memory enroll voiceprint --help`
Expected: command help shows required label and audio arguments

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/integrations/pyannote_client.py src/ambient_memory/pipeline/speaker_matching.py src/ambient_memory/cli.py tests/pipeline/test_speaker_matching.py
git commit -m "feat: add voice enrollment and speaker matching"
```

### Task 9: Add Windowing, Dedup, and the Single Pipeline Worker

**Files:**
- Create: `src/ambient_memory/pipeline/windows.py`
- Create: `src/ambient_memory/pipeline/dedup.py`
- Create: `src/ambient_memory/pipeline/worker.py`
- Test: `tests/pipeline/test_windows.py`
- Test: `tests/pipeline/test_dedup.py`
- Test: `tests/pipeline/test_worker.py`
- Modify: `src/ambient_memory/cli.py`

- [ ] **Step 1: Write failing tests for overlapping-window grouping and canonical merge**

```python
def test_group_chunks_builds_one_window_for_overlapping_sources():
    windows = group_chunks([desk_chunk, room_chunk])
    assert len(windows) == 1

def test_merge_candidates_prefers_local_source_with_higher_confidence():
    utterance = merge_candidates([room_candidate, desk_candidate])
    assert utterance.canonical_source_id == "desk-a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_windows.py tests/pipeline/test_dedup.py tests/pipeline/test_worker.py -q`
Expected: missing worker/window/dedup implementations

- [ ] **Step 3: Implement the worker orchestration**

Requirements:
- poll pending uploaded chunks from Postgres
- group them into 30-60 second windows
- transcribe each source independently
- normalize segments
- identify speakers
- store transcript candidates
- merge duplicate utterances into one canonical row
- record provenance in `utterance_sources`
- mark chunk rows as processed or failed
- expose `ambient-memory worker run-once` and `ambient-memory worker run`

- [ ] **Step 4: Re-run tests and smoke the worker in dry-run mode**

Run: `uv run pytest tests/pipeline/test_windows.py tests/pipeline/test_dedup.py tests/pipeline/test_worker.py -q`
Expected: pipeline tests pass

Run: `uv run ambient-memory worker run-once --dry-run`
Expected: worker logs the number of pending chunks and exits cleanly without mutating data

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/pipeline/windows.py src/ambient_memory/pipeline/dedup.py src/ambient_memory/pipeline/worker.py src/ambient_memory/cli.py tests/pipeline/test_windows.py tests/pipeline/test_dedup.py tests/pipeline/test_worker.py
git commit -m "feat: add transcript pipeline worker"
```

## Chunk 4: Read Surface and Verification

### Task 10: Add the Minimal Search and Replay API

**Files:**
- Create: `src/ambient_memory/api/schemas.py`
- Create: `src/ambient_memory/api/search.py`
- Create: `src/ambient_memory/api/app.py`
- Test: `tests/api/test_search.py`
- Modify: `src/ambient_memory/cli.py`

- [ ] **Step 1: Write failing API tests for transcript search and replay lookup**

```python
def test_search_returns_matching_utterances(client, seeded_db):
    response = client.get("/search", params={"q": "whiteboard bug"})
    assert response.status_code == 200
    assert response.json()["items"][0]["text"] == "whiteboard bug"

def test_utterance_detail_returns_audio_links(client, seeded_db):
    response = client.get("/utterances/utt_123")
    assert "audio" in response.json()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_search.py -q`
Expected: missing API modules or routes

- [ ] **Step 3: Implement the FastAPI read surface**

Routes:
- `GET /health`
- `GET /search?q=...&speaker=...&from=...&to=...`
- `GET /utterances/{utterance_id}`

Behavior:
- use Postgres full-text search on `canonical_utterances.search_vector`
- return timestamps, speaker name, confidence, provenance summary
- return presigned audio URLs for replay

- [ ] **Step 4: Re-run tests and start the local API**

Run: `uv run pytest tests/api/test_search.py -q`
Expected: API tests pass

Run: `uv run ambient-memory api`
Expected: local server starts and `/health` returns `200`

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/api/schemas.py src/ambient_memory/api/search.py src/ambient_memory/api/app.py src/ambient_memory/cli.py tests/api/test_search.py
git commit -m "feat: add transcript search api"
```

### Task 11: Add Worker/API Launchd Templates and End-to-End Verification Docs

**Files:**
- Create: `docs/ops/smoke-test.md`
- Create: `deploy/launchd/com.ambient-memory.worker.plist`
- Create: `deploy/launchd/com.ambient-memory.api.plist`
- Modify: `README.md`
- Create: `tests/test_docs.py`

- [ ] **Step 1: Add a failing checklist test for the smoke-test doc structure**

```python
def test_smoke_test_doc_mentions_capture_worker_api_and_search():
    text = Path("docs/ops/smoke-test.md").read_text()
    assert "agent run --dry-run" in text
    assert "worker run-once --dry-run" in text
    assert "/search" in text
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `uv run pytest tests/test_docs.py -q`
Expected: fail until the smoke-test doc and test helper exist

- [ ] **Step 3: Write the smoke-test guide and service templates**

Checklist must cover:
- start one MacBook agent in dry-run mode
- start one room-box agent in dry-run mode
- perform a short live capture on one source
- confirm chunk upload row in Postgres
- run the worker once
- query `/search`
- open a presigned replay URL

- [ ] **Step 4: Run the full verification suite**

Run: `uv run pytest -q`
Expected: all tests pass

Run: `uv run alembic upgrade head`
Expected: no pending migration errors

Manual smoke:
- follow `docs/ops/smoke-test.md`
- confirm one utterance appears in `/search`
- confirm one replay URL opens from S3

- [ ] **Step 5: Commit**

```bash
git add docs/ops/smoke-test.md deploy/launchd/com.ambient-memory.worker.plist deploy/launchd/com.ambient-memory.api.plist README.md tests/test_docs.py
git commit -m "docs: add ambient memory smoke test and service templates"
```

## Notes for the Implementer

- Keep the repo single-process friendly first. Do not split the worker into multiple services during MVP execution.
- Keep audio as immutable chunk files in S3. Do not attempt in-database blob storage.
- Do not introduce websockets or streaming STT in the MVP.
- Do not add a separate search engine before proving Postgres search is insufficient.
- Store raw vendor payloads for troubleshooting, but keep the canonical transcript format provider-agnostic.
- For hardware-facing code, prefer unit tests around parsers, builders, and orchestration plus manual smoke tests for actual microphone capture.

## Manual Plan Review

This plan was written against the approved spec at `docs/superpowers/specs/2026-04-02-ambient-memory-log-design.md`. Because subagent review was not used in this session, manually verify each chunk before execution against:

- no TODOs or placeholders
- no scope creep beyond the approved MVP
- each task has exact files, commands, and expected outputs
- each new file has one clear responsibility
- each chunk remains under 1000 lines

Plan complete and saved to `docs/superpowers/plans/2026-04-02-ambient-memory-log-mvp.md`. Ready to execute?
