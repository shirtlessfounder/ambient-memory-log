# Silence Filter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** skip obviously silent audio chunks locally before upload so all-day always-on capture does not waste uploads and downstream processing on dead air.

**Architecture:** keep 30-second chunking unchanged, add a conservative local audio-level check in the uploader path, and gate upload on that check. Keep the worker, database, and API untouched, and expose only a minimal config surface for enable/threshold tuning.

**Tech Stack:** Python capture pipeline, `ffmpeg` local audio analysis, Pydantic settings, Markdown docs, pytest

---

## File Map

- Modify: `src/ambient_memory/capture/uploader.py`
- Modify: `src/ambient_memory/config.py`
- Modify: `.env.example`
- Modify: `docs/teammate-setup.md`
- Modify: `docs/ops-machine-setup.md`
- Modify or create: focused tests under `tests/capture/`
- Modify: `tests/test_config.py`

## Chunk 1: Config Surface

### Task 1: Add failing config coverage for silence settings

**Files:**
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add tests that prove:
- capture settings expose `SILENCE_FILTER_ENABLED`
- capture settings expose `SILENCE_MAX_VOLUME_DB`
- defaults are present and parse correctly from `.env`

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -q -k silence`
Expected: FAIL because the settings do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Add the new fields to capture settings in `src/ambient_memory/config.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -q -k silence`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/config.py tests/test_config.py
git commit -m "feat: add silence filter capture settings"
```

## Chunk 2: Upload Path Filtering

### Task 2: Add failing uploader tests for silent-chunk skipping

**Files:**
- Modify or create: focused capture uploader tests

- [ ] **Step 1: Write the failing test**

Add tests that prove:
- silent chunk is skipped before upload
- silent chunk does not create an uploaded DB row
- non-silent chunk still uses the current upload path

Prefer dependency injection for the local analyzer rather than hard-coding subprocess behavior in tests.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/capture -q -k silence`
Expected: FAIL because no silence filter exists yet.

- [ ] **Step 3: Write minimal implementation**

In `src/ambient_memory/capture/uploader.py`:
- add a small local chunk analyzer helper or injectable function
- gate `_upload_entry` on the silence decision
- mark skipped chunks complete locally instead of failed
- log the skip with source id, filename, and measured level

Keep the upload path unchanged for non-silent chunks.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/capture -q -k silence`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/capture/uploader.py tests/capture
git commit -m "feat: skip silent chunks before upload"
```

## Chunk 3: Operator Docs

### Task 3: Update env/docs for silence filtering

**Files:**
- Modify: `.env.example`
- Modify: `docs/teammate-setup.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing docs expectation**

Add assertions that:
- `.env.example` mentions the silence settings
- teammate/ops docs mention the silent-chunk behavior at a high level

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py -q -k silence`
Expected: FAIL until docs are updated.

- [ ] **Step 3: Write minimal implementation**

Update docs to say:
- silent chunks may be skipped locally before upload
- the filter is conservative
- room-mic operators may need threshold tuning later if quiet speech is missed

- [ ] **Step 4: Run docs tests**

Run: `uv run pytest tests/test_docs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .env.example docs/teammate-setup.md docs/ops-machine-setup.md tests/test_docs.py
git commit -m "docs: add silence filter operator guidance"
```

## Chunk 4: Full Verification

### Task 4: Run full verification ring

**Files:**
- No file changes required

- [ ] **Step 1: Run focused config/capture/docs tests**

Run: `uv run pytest tests/test_config.py tests/capture tests/test_docs.py -q`
Expected: PASS

- [ ] **Step 2: Run full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 3: Manual smoke checks**

Run one silent-ish capture and one spoken capture:
- verify silent chunk does not appear downstream
- verify spoken chunk still uploads and transcribes

- [ ] **Step 4: Commit any follow-up fixes if required**

Only if manual verification reveals tuning or logic problems.
