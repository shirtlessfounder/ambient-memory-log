# AssemblyAI Room Path Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** move `room-1` from `Deepgram + pyannote` to `AssemblyAI` while leaving raw capture, storage, search, and non-room processing intact.

**Architecture:** keep the current upload + worker + canonical persistence pipeline, but add an explicit `room-1` branch inside the worker. `room-1` chunks go through a new `AssemblyAIClient` that owns upload, transcript polling, diarized utterance parsing, and known-speaker name mapping; all other sources stay on the current `Deepgram + pyannote` path unchanged.

**Tech Stack:** Python 3.13/3.14, stdlib HTTP (`urllib`), Pydantic settings, SQLAlchemy ORM, pytest, Markdown docs, AssemblyAI Speech Understanding + pre-recorded transcript APIs

---

## File Map

- Create: `src/ambient_memory/integrations/assemblyai_client.py`
- Create: `tests/integrations/test_assemblyai_client.py`
- Modify: `src/ambient_memory/config.py`
- Modify: `src/ambient_memory/pipeline/worker.py`
- Modify: `tests/pipeline/test_worker.py`
- Modify: `tests/test_worker_config.py`
- Modify: `tests/test_config.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `docs/ops/smoke-test.md`
- Modify: `tests/test_docs.py`

## Chunk 1: Worker Config Surface

### Task 1: Add failing config coverage for the AssemblyAI worker key

**Files:**
- Modify: `tests/test_worker_config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add focused tests that prove:
- `load_worker_runtime_config(dry_run=False, env_file=".env.worker")` now treats `ASSEMBLYAI_API_KEY` as a required worker env
- the missing-env error string includes `ASSEMBLYAI_API_KEY`
- `load_settings(WorkerSettings, env_file=".env.worker")` reads `ASSEMBLYAI_API_KEY` from dotenv without disturbing existing keys
- dry-run config still does **not** require any vendor keys

- [ ] **Step 2: Run the focused config ring and confirm it fails**

Run: `uv run pytest tests/test_worker_config.py tests/test_config.py -q -k assemblyai`
Expected: FAIL because the worker config does not expose `ASSEMBLYAI_API_KEY` yet.

- [ ] **Step 3: Write the minimal config implementation**

In `src/ambient_memory/config.py`:
- add `assemblyai_api_key: str | None = Field(default=None, alias="ASSEMBLYAI_API_KEY")` to `WorkerSettings`

In `src/ambient_memory/pipeline/worker.py`:
- add `assemblyai_api_key: str | None` to `WorkerRuntimeConfig`
- teach `load_worker_runtime_config()` to include `ASSEMBLYAI_API_KEY` in the non-dry-run required set
- keep dry-run behavior unchanged so operators can still sanity-check pending work without vendor keys

Do **not** relax the existing `DEEPGRAM_API_KEY` / `PYANNOTE_API_KEY` requirement in this slice; non-room chunks still need them.

- [ ] **Step 4: Re-run the focused config ring**

Run: `uv run pytest tests/test_worker_config.py tests/test_config.py -q -k assemblyai`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/config.py src/ambient_memory/pipeline/worker.py tests/test_worker_config.py tests/test_config.py
git commit -m "feat: add assemblyai worker config"
```

## Chunk 2: AssemblyAI Client

### Task 2: Add failing client tests for upload, polling, and utterance parsing

**Files:**
- Create: `tests/integrations/test_assemblyai_client.py`

- [ ] **Step 1: Write the failing tests**

Add tests that prove the new client:
- uploads raw bytes to `/v2/upload` and reads `upload_url`
- creates a transcript job at `/v2/transcript`
- requests diarized utterances plus speaker identification for a known room roster
- polls `/v2/transcript/{id}` until `completed`
- raises a client error on `error` / malformed payloads
- returns parsed utterances with:
  - `vendor_segment_id`
  - relative start/end timing
  - transcript text
  - diarization speaker hint
  - resolved speaker name when AssemblyAI identifies one
  - raw utterance payload

Use a fake transport in the tests; do not hit the real API.

- [ ] **Step 2: Run the client tests and confirm they fail**

Run: `uv run pytest tests/integrations/test_assemblyai_client.py -q`
Expected: FAIL because the client module does not exist yet.

- [ ] **Step 3: Write the minimal AssemblyAI client**

Create `src/ambient_memory/integrations/assemblyai_client.py` with:
- `AssemblyAIClientError`
- a small stdlib transport layer consistent with the existing integration style
- `AssemblyAIUtterance` dataclass containing:
  - `vendor_segment_id: str | None`
  - `text: str`
  - `speaker_hint: str | None`
  - `speaker_name: str | None`
  - `confidence: float | None`
  - `start_seconds: float`
  - `end_seconds: float`
  - `raw_payload: dict[str, Any]`
- `AssemblyAIClient.transcribe_bytes(...)` that:
  1. uploads the raw bytes to `https://api.assemblyai.com/v2/upload`
  2. submits a transcript request to `https://api.assemblyai.com/v2/transcript`
  3. enables diarized utterances
  4. sends the fixed room roster for speaker identification
  5. polls until `completed` or `error`
  6. parses the returned utterances into `AssemblyAIUtterance` rows

Implementation constraints:
- keep this client self-contained so `worker.py` does not absorb large response-parsing code
- if AssemblyAI does not return a true speaker-identification confidence, leave `speaker_confidence` for persistence as `None` later; do **not** reuse transcript confidence as a fake identity score
- if AssemblyAI returns a diarization label but no mapped name, keep the label in `speaker_hint` and leave `speaker_name=None`

Use the official AssemblyAI room path described in:
- speaker identification docs: `https://www.assemblyai.com/docs/speech-understanding/speaker-identification`
- diarization docs: `https://www.assemblyai.com/docs/pre-recorded-audio/speaker-diarization`

- [ ] **Step 4: Re-run the client tests**

Run: `uv run pytest tests/integrations/test_assemblyai_client.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/integrations/assemblyai_client.py tests/integrations/test_assemblyai_client.py
git commit -m "feat: add assemblyai room transcription client"
```

## Chunk 3: Worker Branching

### Task 3: Add failing worker coverage for `room-1` AssemblyAI routing

**Files:**
- Modify: `tests/pipeline/test_worker.py`

- [ ] **Step 1: Write the failing worker tests**

Add tests that prove:
- `room-1` calls `AssemblyAIClient` and does **not** call `DeepgramClient` or `PyannoteClient`
- non-room chunks still call `DeepgramClient` and `PyannoteClient` and do **not** call `AssemblyAIClient`
- `room-1` transcript candidates persist with `vendor="assemblyai"`
- canonical utterances can carry room speaker names directly from AssemblyAI output
- an AssemblyAI room failure marks the chunk failed and does not silently fall back to the legacy path

Prefer adding a `FakeAssemblyAIClient` next to the existing fake vendor clients.

- [ ] **Step 2: Run the focused worker tests and confirm they fail**

Run: `uv run pytest tests/pipeline/test_worker.py -q -k assemblyai`
Expected: FAIL because the worker cannot route room chunks to AssemblyAI yet.

- [ ] **Step 3: Write the minimal worker implementation**

In `src/ambient_memory/pipeline/worker.py`:
- add `assemblyai_client` as a `PipelineWorker` dependency
- import and construct `AssemblyAIClient` in `build_worker()`
- add an explicit source gate such as `ROOM_ASSEMBLY_SOURCE_ID = "room-1"`
- add a fixed room roster constant for the first slice:
  - `("Dylan", "Niyant", "Alex", "Jakub")`
- branch processing per chunk:
  - `room-1`:
    - load audio bytes from S3
    - call `assemblyai_client.transcribe_bytes(...)`
    - persist `TranscriptCandidate` rows with `vendor="assemblyai"`
    - use the returned diarization label for `speaker_hint`
    - keep `speaker_confidence=None`
    - build `DedupCandidate` rows with `speaker_name` from AssemblyAI
    - skip pyannote voiceprint loading and matching entirely
  - non-room:
    - leave the current `Deepgram + pyannote` path intact

Worker structure constraints:
- keep the room-path logic in small helpers so `worker.py` stays below the repo’s “hard to reason about” threshold
- avoid any fallback from AssemblyAI to Deepgram/pyannote inside the same run
- only require the dependencies each branch actually uses while processing that chunk; do not crash a room-only window just because no pyannote call is needed there

- [ ] **Step 4: Re-run the focused worker tests**

Run: `uv run pytest tests/pipeline/test_worker.py -q -k assemblyai`
Expected: PASS

- [ ] **Step 5: Run the broader worker ring**

Run: `uv run pytest tests/pipeline/test_worker.py -q`
Expected: PASS and existing non-room behavior remains green.

- [ ] **Step 6: Commit**

```bash
git add src/ambient_memory/pipeline/worker.py tests/pipeline/test_worker.py
git commit -m "feat: route room-1 through assemblyai"
```

## Chunk 4: Operator Env And Verification Docs

### Task 4: Add failing docs expectations for the new room worker path

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `docs/ops/smoke-test.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing docs tests**

Add assertions that:
- `.env.example` mentions `ASSEMBLYAI_API_KEY`
- `README.md` mentions that `room-1` uses AssemblyAI while non-room sources still use Deepgram + pyannote
- `docs/ops-machine-setup.md` lists `ASSEMBLYAI_API_KEY` in `.env.worker`
- `docs/ops/smoke-test.md` tells operators to verify recent `room-1` rows show `vendor='assemblyai'`

- [ ] **Step 2: Run the docs tests and confirm they fail**

Run: `uv run pytest tests/test_docs.py -q -k assembly`
Expected: FAIL because the docs do not mention the new worker path yet.

- [ ] **Step 3: Write the minimal docs changes**

Update `.env.example`:
- add `ASSEMBLYAI_API_KEY=replace-me` near the other worker vendor keys

Update `README.md`:
- state that the current room source path uses AssemblyAI
- keep the rest of the pipeline description high level and concise

Update `docs/ops-machine-setup.md`:
- add `ASSEMBLYAI_API_KEY` to the `.env.worker` required values
- note that the room mic still captures raw audio locally, but `room-1` transcript + labeling now happen in AssemblyAI inside the worker

Update `docs/ops/smoke-test.md`:
- add a room-path verification step that checks:
  - fresh `room-1` chunk exists
  - worker processes it
  - resulting transcript candidate rows show `vendor='assemblyai'`
  - canonical utterances show materially more speaker names than the old near-zero baseline

- [ ] **Step 4: Re-run the docs tests**

Run: `uv run pytest tests/test_docs.py -q -k assembly`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md docs/ops-machine-setup.md docs/ops/smoke-test.md tests/test_docs.py
git commit -m "docs: add assemblyai room path guidance"
```

## Chunk 5: Full Verification And Live Smoke

### Task 5: Run the full verification ring

**Files:**
- No additional file changes required unless verification exposes a bug

- [ ] **Step 1: Run the focused implementation ring**

Run: `uv run pytest tests/test_worker_config.py tests/test_config.py tests/integrations/test_assemblyai_client.py tests/pipeline/test_worker.py tests/test_docs.py -q`
Expected: PASS

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 3: Run a room-only live smoke on Dylan’s machine**

Preconditions:
- `.env.worker` contains `ASSEMBLYAI_API_KEY`
- Owl capture is still producing fresh `room-1` chunks
- worker + API are running from the updated main branch

Run:

```bash
cd /Users/dylanvu/Projects/ambient-memory-log
uv run ambient-memory worker run-once
```

Expected:
- room backlog processes without fallback errors
- latest `room-1` chunk is marked `processed`

Then verify recent `room-1` transcript candidates with a repo-local Python query:

```bash
cd /Users/dylanvu/Projects/ambient-memory-log
uv run python - <<'PY'
from sqlalchemy import select
from ambient_memory.config import DatabaseSettings, WorkerSettings, load_settings
from ambient_memory.db import build_session_factory
from ambient_memory.models import TranscriptCandidate

settings = load_settings(WorkerSettings, env_file=".env.worker")
Session = build_session_factory(
    DatabaseSettings(
        database_url=settings.database_url,
        database_ssl_root_cert=settings.database_ssl_root_cert,
    )
)

with Session() as session:
    rows = session.execute(
        select(
            TranscriptCandidate.source_id,
            TranscriptCandidate.vendor,
            TranscriptCandidate.text,
            TranscriptCandidate.speaker_hint,
            TranscriptCandidate.started_at,
        )
        .where(TranscriptCandidate.source_id == "room-1")
        .order_by(TranscriptCandidate.created_at.desc())
        .limit(5)
    ).all()
    for row in rows:
        print(row)
PY
```

Expected:
- rows show `source_id='room-1'`
- rows show `vendor='assemblyai'`
- text is non-empty
- `speaker_hint` is populated when diarization produced a speaker label

- [ ] **Step 4: Verify search/API still read the same canonical model**

Run the updated flow in `docs/ops/smoke-test.md`.

Expected:
- `/search` still returns canonical utterances
- replay audio links still resolve against the same raw chunks
- room turns now have materially more names than the old near-zero baseline

- [ ] **Step 5: Commit any verification-driven follow-up fixes**

If live verification exposes a real bug, add the failing test first, fix it, re-run the affected ring, then commit with a targeted conventional commit message.

## Review Notes

- This plan intentionally keeps non-room sources on the legacy vendor path.
- This plan intentionally does **not** add enhancement / denoise yet.
- This plan intentionally does **not** remove pyannote voiceprint enrollment yet, because non-room sources still depend on it.
