# Dual Capture Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** add a one-command, one-service dual-capture mode that starts teammate and room-mic capture together while preserving the existing one-source-per-process architecture.

**Architecture:** add a small orchestration layer in the CLI that supervises two child capture commands, then expose it through one wrapper script and one `launchd` plist. Keep `start-teammate` and `start-room-mic` as the underlying primitives and update docs/tests to make the new mode discoverable and operator-friendly.

**Tech Stack:** Python CLI (`typer`, `subprocess`), macOS `launchd`, POSIX shell, Markdown docs, pytest

---

## File Map

- Create: `deploy/launchd/com.ambient-memory.dual-capture.plist`
- Create: `scripts/start-dual-capture.sh`
- Modify: `src/ambient_memory/cli.py`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_docs.py`

## Chunk 1: CLI Orchestration

### Task 1: Add failing CLI coverage for `start-dual-capture`

**Files:**
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add tests that prove:
- `start-dual-capture` appears in top-level help
- the command starts two child processes, one for teammate and one for room mic
- the command exits non-zero if either env file is missing

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -q -k dual_capture`
Expected: FAIL because the command does not exist yet.

- [ ] **Step 3: Write minimal implementation**

In `src/ambient_memory/cli.py`, add:
- a small process runner helper for dual capture
- `start-dual-capture`

Keep it orchestration-only:
- validate `.env.teammate`
- validate `.env.room-mic`
- spawn `uv run ambient-memory start-teammate`
- spawn `uv run ambient-memory start-room-mic`
- wait and coordinate shutdown

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -q -k dual_capture`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ambient_memory/cli.py tests/test_cli.py
git commit -m "feat: add dual capture cli orchestration"
```

## Chunk 2: Startup Assets

### Task 2: Add dual-capture wrapper script

**Files:**
- Create: `scripts/start-dual-capture.sh`

- [ ] **Step 1: Write the failing docs/template expectation**

Extend `tests/test_docs.py` to require:
- `scripts/start-dual-capture.sh` exists
- it references `.env.teammate`
- it references `.env.room-mic`
- it runs `ambient-memory start-dual-capture`

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py -q -k dual`
Expected: FAIL because the script does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create a shell wrapper that:
- resolves repo root
- checks `.env.teammate`
- checks `.env.room-mic`
- `exec`s `uv run ambient-memory start-dual-capture`

- [ ] **Step 4: Run syntax check**

Run: `zsh -n scripts/start-dual-capture.sh`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/start-dual-capture.sh tests/test_docs.py
git commit -m "feat: add dual capture startup wrapper"
```

### Task 3: Add dual-capture `launchd` plist

**Files:**
- Create: `deploy/launchd/com.ambient-memory.dual-capture.plist`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing test expectation**

Add assertions that the new plist:
- exists
- references `start-dual-capture.sh`
- has `RunAtLoad`
- has `KeepAlive`
- has stdout/stderr log paths

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py -q -k dual`
Expected: FAIL because the plist does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create a plist modeled on the existing capture agent template, but pointing at the dual wrapper.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_docs.py -q -k dual`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add deploy/launchd/com.ambient-memory.dual-capture.plist tests/test_docs.py
git commit -m "feat: add dual capture launchd template"
```

## Chunk 3: Operator Docs

### Task 4: Update README and ops docs for dual mode

**Files:**
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing test expectation**

Add assertions that:
- README mentions `start-dual-capture`
- ops machine setup mentions dual capture mode
- ops machine setup references the dual-capture plist/wrapper or command

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py -q -k dual`
Expected: FAIL until docs are updated.

- [ ] **Step 3: Write minimal implementation**

Update docs so operators can understand:
- when to use teammate-only
- when to use room-only
- when to use dual capture
- how dual capture fits with worker/API on the same machine

Keep the teammate doc simple unless it needs a short cross-reference.

- [ ] **Step 4: Run docs tests**

Run: `uv run pytest tests/test_docs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md docs/ops-machine-setup.md tests/test_docs.py
git commit -m "docs: add dual capture operator guidance"
```

## Chunk 4: Full Verification

### Task 5: Run the full verification ring

**Files:**
- No file changes required

- [ ] **Step 1: Run focused tests**

Run: `uv run pytest tests/test_cli.py tests/test_docs.py -q`
Expected: PASS

- [ ] **Step 2: Run full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 3: Manual smoke check**

Run:

```bash
uv run ambient-memory start-dual-capture
```

Then:
- speak a short unique phrase
- stop after one chunk closes
- run `uv run ambient-memory worker run-once`
- verify both `desk-a` and `room-1` appear in downstream provenance

- [ ] **Step 4: Commit final verification-only follow-up if needed**

Only if code/doc fixes were required after manual verification.
