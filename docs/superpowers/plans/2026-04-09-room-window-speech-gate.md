# Room Window Speech Gate Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** reduce AssemblyAI cost on `room-1` by permanently skipping stitched `600s` room windows that do not contain enough speech to justify transcription and naming.

**Architecture:** keep the existing `30s` capture/upload path unchanged, including the chunk-level silence filter. Add a second worker-side speech-duration gate after room chunks are stitched into a `600s` window and before the AssemblyAI call. Low-speech windows should be logged and marked processed without creating transcript rows or retry loops; spoken windows should continue through the current delayed room Assembly path unchanged.

**Tech Stack:** Python 3.13/3.14, ffmpeg, stdlib subprocess, SQLAlchemy ORM, Pydantic settings, pytest, Markdown docs

---

## File Map

- Create: `src/ambient_memory/pipeline/room_speech.py`
- Modify: `src/ambient_memory/config.py`
- Modify: `src/ambient_memory/pipeline/worker.py`
- Modify: `tests/test_worker_config.py`
- Modify: `tests/pipeline/test_worker.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `tests/test_docs.py`

## Chunk 1: Config And Worker Contract

### Task 1: Add failing config coverage for the room speech threshold

**Files:**
- Modify: `tests/test_worker_config.py`
- Modify: `src/ambient_memory/config.py`
- Modify: `src/ambient_memory/pipeline/worker.py`

- [ ] **Step 1: Write the failing tests**

Add focused tests that prove:
- `WorkerSettings` exposes `ROOM_MIN_SPEECH_SECONDS`
- dry-run worker config returns the default threshold
- non-dry-run worker config loads an explicit threshold from `.env.worker`
- non-positive values fail fast through worker config loading

- [ ] **Step 2: Run the focused config tests to verify they fail**

Run: `uv run pytest tests/test_worker_config.py -q -k speech`
Expected: FAIL because the new worker setting does not exist yet.

- [ ] **Step 3: Write the minimal implementation**

In `src/ambient_memory/config.py`:
- add `room_min_speech_seconds: float = Field(default=20.0, alias="ROOM_MIN_SPEECH_SECONDS", gt=0)`

In `src/ambient_memory/pipeline/worker.py`:
- extend `WorkerRuntimeConfig` with `room_min_speech_seconds: float = 20.0`
- thread the value through `load_worker_runtime_config(...)`, `run_worker_once(...)`, and `build_worker(...)`
- pass the value into `PipelineWorker`

- [ ] **Step 4: Re-run the focused config tests**

Run: `uv run pytest tests/test_worker_config.py -q -k speech`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/config.py src/ambient_memory/pipeline/worker.py tests/test_worker_config.py
git commit -m "feat: add room speech gate config"
```

## Chunk 2: Room Speech Analyzer And Worker Gating

### Task 2: Add failing worker tests for low-speech room windows

**Files:**
- Create: `src/ambient_memory/pipeline/room_speech.py`
- Modify: `tests/pipeline/test_worker.py`
- Modify: `src/ambient_memory/pipeline/worker.py`

- [ ] **Step 1: Write the failing worker tests**

Add worker tests that prove:
- a ready `room-1` batch with measured speech below threshold does not call AssemblyAI
- low-speech windows do not create transcript candidates
- low-speech windows do not create canonical utterances
- low-speech windows are marked `processed`, not left `uploaded`, and do not retry
- spoken room windows at or above threshold still call AssemblyAI and publish normally

Use dependency injection for the speech analyzer so the tests do not shell out to `ffmpeg`.

- [ ] **Step 2: Run the focused worker tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_worker.py -q -k speech`
Expected: FAIL because the worker currently always calls AssemblyAI for ready room batches.

- [ ] **Step 3: Write the minimal analyzer helper**

Create `src/ambient_memory/pipeline/room_speech.py` with:
- a small callable-friendly helper, for example `measure_speech_seconds(audio_bytes: bytes, *, ffmpeg_binary: str = "ffmpeg") -> float`
- an `ffmpeg`-based implementation that estimates speech-active duration from the stitched WAV
- clear `RuntimeError` messages when analysis fails

Keep it isolated from DB and ORM code so it can be injected or replaced in tests.

- [ ] **Step 4: Write the minimal worker implementation**

In `src/ambient_memory/pipeline/worker.py`:
- accept `room_min_speech_seconds` and an injectable `measure_room_speech_seconds` dependency on `PipelineWorker`
- in `_process_room_batch(...)`, stitch the room audio as today, then measure speech seconds before calling AssemblyAI
- if measured speech is below threshold:
  - log the skip with room start/end, chunk count, measured speech seconds, and threshold
  - mark the room chunks `processed`
  - do not call AssemblyAI
  - do not persist transcript candidates
  - do not persist canonical utterances
  - return `True` so `run_once()` counts the chunks as handled
- if measured speech meets threshold:
  - continue on the current delayed AssemblyAI path unchanged

Failure handling:
- if the speech analyzer itself errors, let the batch stay retryable by raising instead of silently skipping
- do not route low-speech room windows into the failure path; they are intentional terminal skips

- [ ] **Step 5: Re-run the focused worker tests**

Run: `uv run pytest tests/pipeline/test_worker.py -q -k speech`
Expected: PASS

- [ ] **Step 6: Run the broader room worker ring**

Run: `uv run pytest tests/pipeline/test_worker.py -q -k room`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/ambient_memory/pipeline/room_speech.py src/ambient_memory/pipeline/worker.py tests/pipeline/test_worker.py
git commit -m "feat: skip low-speech room windows"
```

## Chunk 3: Docs And Rollout

### Task 3: Document the new room speech gate and verify live rollout

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing docs tests**

Add focused docs assertions that mention:
- `ROOM_MIN_SPEECH_SECONDS`
- default `20` second threshold
- low-speech room windows are skipped permanently and do not retry
- chunk-level silence filtering still exists independently of the room-window gate

- [ ] **Step 2: Run the focused docs tests to verify they fail**

Run: `uv run pytest tests/test_docs.py -q -k speech`
Expected: FAIL because the docs do not mention the new gate yet.

- [ ] **Step 3: Update the docs minimally**

In `.env.example`, `README.md`, and `docs/ops-machine-setup.md`:
- add `ROOM_MIN_SPEECH_SECONDS=20`
- explain that the worker applies a second speech gate to stitched room windows before AssemblyAI
- explain that low-speech windows are skipped terminally, not retried
- keep teammate-facing capture docs unchanged unless they mention worker internals directly

- [ ] **Step 4: Re-run the focused docs tests**

Run: `uv run pytest tests/test_docs.py -q -k speech`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Roll out on Dylan's machine**

Runtime-only steps:
- add `ROOM_MIN_SPEECH_SECONDS=20` to local `.env.worker`
- restart `com.ambient-memory.worker`
- confirm `room-1` chunks still upload every `30s`
- confirm quiet room windows stop reaching AssemblyAI
- confirm spoken room windows still publish through the delayed named path

- [ ] **Step 7: Commit**

```bash
git add .env.example README.md docs/ops-machine-setup.md tests/test_docs.py
git commit -m "docs: add room speech gate guidance"
```
