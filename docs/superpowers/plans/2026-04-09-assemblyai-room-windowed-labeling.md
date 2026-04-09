# AssemblyAI Room Windowed Labeling Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** improve `room-1` speaker labeling by batching uploaded `30s` room chunks into delayed `600s` AssemblyAI windows and only publishing room output when real roster names are returned.

**Architecture:** keep raw room capture, upload, and storage unchanged, but split `room-1` off from the current per-chunk publish path. The worker will load a local room roster file, build fixed `600s` room windows from contiguous uploaded chunks, stitch those chunks, send richer speaker profiles to AssemblyAI, and withhold canonical room output until accepted named batches are ready.

**Tech Stack:** Python 3.13/3.14, stdlib HTTP, SQLAlchemy ORM, Pydantic settings, pytest, JSON config, Markdown docs

---

## File Map

- Create: `config/room-speakers.json`
- Create: `src/ambient_memory/pipeline/room_windows.py`
- Modify: `src/ambient_memory/config.py`
- Modify: `src/ambient_memory/integrations/assemblyai_client.py`
- Modify: `src/ambient_memory/pipeline/worker.py`
- Create or modify: `tests/pipeline/test_room_windows.py`
- Modify: `tests/integrations/test_assemblyai_client.py`
- Modify: `tests/pipeline/test_worker.py`
- Modify: `tests/test_worker_config.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `docs/ops/smoke-test.md`
- Modify: `tests/test_docs.py`

## Chunk 1: Config And Roster Input

### Task 1: Add failing config coverage for room-window settings and roster path

**Files:**
- Modify: `tests/test_worker_config.py`
- Modify: `src/ambient_memory/config.py`
- Create: `config/room-speakers.json`

- [ ] **Step 1: Write the failing tests**

Add focused tests that prove:
- `WorkerSettings` exposes `ROOM_SPEAKER_ROSTER_PATH`
- `WorkerSettings` exposes `ROOM_ASSEMBLY_WINDOW_SECONDS`
- `WorkerSettings` exposes `ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS`
- non-dry-run worker config includes those values
- missing roster path or missing window config yields a clear runtime/config error

- [ ] **Step 2: Run the focused config tests to verify they fail**

Run: `uv run pytest tests/test_worker_config.py -q -k room`
Expected: FAIL because the room-window settings do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

In `src/ambient_memory/config.py`:
- add optional worker fields:
  - `room_speaker_roster_path: str | None = Field(default=None, alias="ROOM_SPEAKER_ROSTER_PATH")`
  - `room_assembly_window_seconds: int = Field(default=600, alias="ROOM_ASSEMBLY_WINDOW_SECONDS", gt=0)`
  - `room_assembly_idle_flush_seconds: int = Field(default=120, alias="ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS", gt=0)`

In `src/ambient_memory/pipeline/worker.py`:
- extend `WorkerRuntimeConfig` with the new room fields
- require `ROOM_SPEAKER_ROSTER_PATH` for non-dry-run worker config
- keep dry-run behavior unchanged

Create `config/room-speakers.json` with the approved roster:

```json
[
  {
    "name": "Dylan",
    "description": "Male voice. Often seems to drive or steer discussion. Tends to connect product questions with systems and operational concerns, and can move across topics quickly in a single thread.",
    "aliases": ["dylan", "dylan vu"]
  },
  {
    "name": "Niyant",
    "description": "Male voice. Often comes across as analytical and technically grounded. Tends to focus on implementation details, constraints, and concrete execution tradeoffs.",
    "aliases": ["niyant"]
  },
  {
    "name": "Alex",
    "description": "Male voice. Often sounds reflective, structured, and evaluative. Tends to frame discussions around reasoning, diagnosis, and whether an approach is actually working.",
    "aliases": ["alex", "alexander janiak"]
  },
  {
    "name": "Jakub",
    "description": "Male voice. Often sounds collaborative and coordination-oriented. Tends to frame things in terms of hypotheses, alignment, community, and how people work together.",
    "aliases": ["jakub", "jakub janiak"]
  }
]
```

- [ ] **Step 4: Re-run the focused config tests**

Run: `uv run pytest tests/test_worker_config.py -q -k room`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/config.py src/ambient_memory/pipeline/worker.py tests/test_worker_config.py config/room-speakers.json
git commit -m "feat: add room window worker config"
```

## Chunk 2: AssemblyAI Roster Payload And Name Acceptance

### Task 2: Add failing client tests for richer speaker profiles and unnamed diarization responses

**Files:**
- Modify: `tests/integrations/test_assemblyai_client.py`
- Modify: `src/ambient_memory/integrations/assemblyai_client.py`

- [ ] **Step 1: Write the failing tests**

Add tests that prove:
- the client can accept structured room speaker profiles, not just bare names
- the transcript payload uses the richer speaker-identification request shape
- responses that map `A -> A` or otherwise fail to produce real roster names are treated as unnamed
- client parsing never turns bare diarization labels into `speaker_name`

- [ ] **Step 2: Run the client tests to verify they fail**

Run: `uv run pytest tests/integrations/test_assemblyai_client.py -q -k speaker`
Expected: FAIL because the client still only accepts `speaker_names`.

- [ ] **Step 3: Write the minimal implementation**

In `src/ambient_memory/integrations/assemblyai_client.py`:
- add a small dataclass for room speaker profiles, for example:
  - `name`
  - `description`
  - `aliases`
- change `transcribe_bytes(...)` to accept structured room speakers rather than a tuple of names
- update payload construction to send the richer speaker profile shape supported by AssemblyAI speaker identification
- tighten mapping logic so:
  - diarization labels like `A/B/C` are preserved only as `speaker_hint`
  - `speaker_name` is set only when the mapped value is a real roster person and not just a label echo
  - `A -> A` is treated as unnamed

Keep the client self-contained. Do not push roster parsing or acceptance heuristics down into `worker.py`.

- [ ] **Step 4: Re-run the focused client tests**

Run: `uv run pytest tests/integrations/test_assemblyai_client.py -q -k speaker`
Expected: PASS

- [ ] **Step 5: Run the full client test file**

Run: `uv run pytest tests/integrations/test_assemblyai_client.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ambient_memory/integrations/assemblyai_client.py tests/integrations/test_assemblyai_client.py
git commit -m "feat: enrich assemblyai room speaker profiles"
```

## Chunk 3: Room Window Selection Helper

### Task 3: Add failing tests for room-only `600s` batching and idle flush

**Files:**
- Create: `src/ambient_memory/pipeline/room_windows.py`
- Create or modify: `tests/pipeline/test_room_windows.py`

- [ ] **Step 1: Write the failing tests**

Add tests that prove a room window helper can:
- group contiguous `room-1` chunks into fixed `600s` windows
- ignore non-room chunks
- leave too-short room spans pending until idle flush conditions are met
- flush a shorter trailing room span when the newest room chunk is older than the configured idle threshold
- avoid mixing gaps / discontiguous chunks into one room batch

Use plain dataclasses or lightweight value objects in the tests. Keep it pure and deterministic.

- [ ] **Step 2: Run the focused helper tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_room_windows.py -q`
Expected: FAIL because the helper does not exist yet.

- [ ] **Step 3: Write the minimal helper**

Create `src/ambient_memory/pipeline/room_windows.py` with:
- a small input type representing pending room chunks
- a helper that returns:
  - room batches ready to process now
  - room chunks still pending because they have not reached `600s` and are not idle-flush eligible

Helper constraints:
- fixed target window: `600s`
- fixed idle flush threshold from config
- contiguous chunks only
- pure logic only; no DB or S3 access here

- [ ] **Step 4: Re-run the helper tests**

Run: `uv run pytest tests/pipeline/test_room_windows.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/pipeline/room_windows.py tests/pipeline/test_room_windows.py
git commit -m "feat: add room assembly window batching"
```

## Chunk 4: Worker Gating And Room Batch Publishing

### Task 4: Add failing worker tests for hidden-until-named room publication

**Files:**
- Modify: `tests/pipeline/test_worker.py`
- Modify: `src/ambient_memory/pipeline/worker.py`

- [ ] **Step 1: Write the failing worker tests**

Add tests that prove:
- `room-1` no longer publishes per `30s` chunk
- insufficient room context stays pending and invisible
- a ready `600s` room batch stitches and calls AssemblyAI once
- accepted named room batch persists transcript candidates and canonical utterances
- `A/B/C`-only room responses do not publish canonical rows
- non-room sources continue using the existing path unchanged

The worker tests should also pin what happens to the underlying chunk statuses:
- accepted room batch -> mark processed
- too-short or unnamed room batch -> remain retryable / pending

- [ ] **Step 2: Run the focused worker tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_worker.py -q -k room`
Expected: FAIL because room gating/batching is not implemented yet.

- [ ] **Step 3: Write the minimal worker implementation**

In `src/ambient_memory/pipeline/worker.py`:
- load the room roster file once per worker run
- split room and non-room pending work
- use `room_windows.py` to identify room batches ready to process
- keep too-short / not-idle-flushed room chunks out of the current run result
- stitch room batch audio by concatenating chunk bytes in order
- send the stitched bytes plus the structured roster to AssemblyAI
- accept room output only if at least one utterance resolves to a real roster name
- publish accepted room transcript candidates / canonical utterances
- keep unnamed-only room batches hidden and retryable

Implementation constraints:
- preserve the current non-room path with minimal churn
- avoid schema changes if possible
- keep room batching logic out of the generic legacy path
- do not mark hidden room chunks `failed` just because naming quality was insufficient

- [ ] **Step 4: Re-run the focused worker tests**

Run: `uv run pytest tests/pipeline/test_worker.py -q -k room`
Expected: PASS

- [ ] **Step 5: Run the broader worker ring**

Run: `uv run pytest tests/pipeline/test_worker.py tests/pipeline/test_room_windows.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ambient_memory/pipeline/worker.py tests/pipeline/test_worker.py src/ambient_memory/pipeline/room_windows.py tests/pipeline/test_room_windows.py
git commit -m "feat: delay room publication until named batch is ready"
```

## Chunk 5: Docs And Operator Guidance

### Task 5: Add failing docs expectations for the delayed room path

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `docs/ops/smoke-test.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing docs tests**

Add assertions that docs mention:
- `ROOM_SPEAKER_ROSTER_PATH`
- `ROOM_ASSEMBLY_WINDOW_SECONDS`
- delayed room visibility / hidden-until-named behavior
- `A/B/C` are not surfaced as names
- the expected `~10 minute` room-output delay

- [ ] **Step 2: Run the focused docs tests to verify they fail**

Run: `uv run pytest tests/test_docs.py -q -k room`
Expected: FAIL because the delayed room-window behavior is not documented yet.

- [ ] **Step 3: Write the minimal docs changes**

Update `.env.example` with:
- `ROOM_SPEAKER_ROSTER_PATH=./config/room-speakers.json`
- `ROOM_ASSEMBLY_WINDOW_SECONDS=600`
- `ROOM_ASSEMBLY_IDLE_FLUSH_SECONDS=120`

Update `README.md` and `docs/ops-machine-setup.md` to explain:
- `room-1` is delayed on purpose
- room output appears only after a named AssemblyAI batch succeeds
- unlabeled diarization letters are suppressed

Update `docs/ops/smoke-test.md` to tell operators to verify:
- fresh `30s` room chunks are still uploading
- room searchable output appears after the delayed window
- returned room names are real roster names, not `A/B/C`

- [ ] **Step 4: Re-run the focused docs tests**

Run: `uv run pytest tests/test_docs.py -q -k room`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md docs/ops-machine-setup.md docs/ops/smoke-test.md tests/test_docs.py
git commit -m "docs: add delayed room labeling guidance"
```

## Chunk 6: Full Verification And Live Smoke

### Task 6: Run the full verification ring

**Files:**
- No new files unless verification exposes a bug

- [ ] **Step 1: Run the focused implementation ring**

Run: `uv run pytest tests/test_worker_config.py tests/integrations/test_assemblyai_client.py tests/pipeline/test_room_windows.py tests/pipeline/test_worker.py tests/test_docs.py -q`
Expected: PASS

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 3: Run live room smoke on Dylan’s machine**

Preconditions:
- `.env.worker` contains:
  - `ASSEMBLYAI_API_KEY`
  - `ROOM_SPEAKER_ROSTER_PATH`
  - `ROOM_ASSEMBLY_WINDOW_SECONDS=600`
- roster file exists at the configured path
- room mic capture is still active
- worker is restarted after the new env/config changes

Verify:
- fresh `room-1` chunks still upload every `30s`
- room output does not appear immediately
- after roughly `10 minutes`, the delayed room batch either:
  - appears with real names, or
  - remains hidden if naming quality is still insufficient
- `A/B/C` do not appear as surfaced speaker names in canonical/search rows

- [ ] **Step 4: If live smoke reveals a bug, add the failing test first**

Keep any follow-up fix narrow and rooted in the observed failure.

- [ ] **Step 5: Commit any verification-driven follow-up fix**

Only if needed.

## Review Notes

- This plan intentionally optimizes for room label quality over room latency.
- This plan intentionally does not attempt a `300 -> 600` escalation path.
- This plan intentionally avoids schema changes unless the current pending/processed model makes them unavoidable.
