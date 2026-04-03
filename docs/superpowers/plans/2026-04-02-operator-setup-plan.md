# Operator Setup Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** add a teammate-friendly startup path for MacBook capture agents using one generic `launchd` template, one wrapper script, and one concise operator runbook.

**Architecture:** keep teammate-specific configuration in `.env`, keep service wiring generic in a single capture-agent plist, and hide startup command details behind a small shell wrapper. Update the README and docs tests so the operator path is discoverable and locked in.

**Tech Stack:** Python CLI (`uv`), macOS `launchd`, POSIX shell, Markdown docs, pytest

---

## File Map

- Create: `docs/operator-setup.md`
- Create: `deploy/launchd/com.ambient-memory.capture-agent.plist`
- Create: `scripts/start-capture-agent.sh`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `tests/test_docs.py`

## Chunk 1: Capture Startup Assets

### Task 1: Add docs tests for the new operator path

**Files:**
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write the failing test**

Add assertions that:
- `docs/operator-setup.md` exists and mentions `.env`, `CAPTURE_DEVICE_NAME`, `launchctl`, and log inspection
- `deploy/launchd/com.ambient-memory.capture-agent.plist` exists and references the wrapper script

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py -q`
Expected: FAIL because the operator doc and capture plist do not exist yet.

- [ ] **Step 3: Add the minimal assets to satisfy the test later**

Do not implement yet. Use this red step to confirm the missing-file failure is real.

- [ ] **Step 4: Commit**

```bash
git add tests/test_docs.py
git commit -m "test: add operator setup doc expectations"
```

### Task 2: Add the generic capture-agent wrapper script

**Files:**
- Create: `scripts/start-capture-agent.sh`

- [ ] **Step 1: Write the failing test surrogate**

Since the repo does not currently have shell script tests, define the expected contract in the file header and in `tests/test_docs.py` string assertions:
- script loads repo `.env`
- script requires `CAPTURE_DEVICE_NAME`
- script runs `uv run ambient-memory agent run --device ...`

- [ ] **Step 2: Verify the current repo has no such script**

Run: `test -f scripts/start-capture-agent.sh`
Expected: exit non-zero.

- [ ] **Step 3: Write minimal implementation**

Create a small shell script that:
- uses `#!/bin/zsh`
- resolves repo root from script location
- `cd`s to repo root
- fails if `.env` is missing
- exports vars from `.env`
- fails if `CAPTURE_DEVICE_NAME` is missing
- `exec`s `uv run ambient-memory agent run --device "$CAPTURE_DEVICE_NAME"`

- [ ] **Step 4: Run a smoke check**

Run: `zsh -n scripts/start-capture-agent.sh`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/start-capture-agent.sh
git commit -m "feat: add capture agent startup wrapper"
```

### Task 3: Add the generic capture-agent launchd template

**Files:**
- Create: `deploy/launchd/com.ambient-memory.capture-agent.plist`

- [ ] **Step 1: Write the failing test expectation**

Extend `tests/test_docs.py` to require:
- label for capture agent
- wrapper script path in `ProgramArguments`
- `WorkingDirectory`
- `RunAtLoad`
- `KeepAlive`
- stdout/stderr log paths

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py -q`
Expected: FAIL because the capture plist is missing.

- [ ] **Step 3: Write minimal implementation**

Create a generic template modeled after the existing worker/API plists that:
- calls `scripts/start-capture-agent.sh`
- keeps placeholders only for user-specific filesystem paths
- does not inline teammate-specific env vars beyond basic `PATH`

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_docs.py -q`
Expected: PASS for the new file-presence and key-string assertions.

- [ ] **Step 5: Commit**

```bash
git add deploy/launchd/com.ambient-memory.capture-agent.plist tests/test_docs.py
git commit -m "feat: add capture agent launchd template"
```

## Chunk 2: Operator Documentation

### Task 4: Tighten the env template for capture-agent setup

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Write the failing test expectation**

Add assertions in `tests/test_docs.py` that `.env.example` mentions `CAPTURE_DEVICE_NAME`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py -q`
Expected: FAIL because `.env.example` does not mention the device variable.

- [ ] **Step 3: Write minimal implementation**

Add:
- `CAPTURE_DEVICE_NAME=Built-in Microphone`

Keep the file generic and avoid team-specific values.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_docs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add .env.example tests/test_docs.py
git commit -m "docs: add capture device env example"
```

### Task 5: Add the teammate-facing operator runbook

**Files:**
- Create: `docs/operator-setup.md`

- [ ] **Step 1: Write the failing test expectation**

Extend `tests/test_docs.py` to require the doc to cover:
- prerequisites (`uv`, `ffmpeg`)
- clone/sync
- `.env`
- `ambient-memory list-devices`
- `agent run --dry-run`
- `launchctl bootstrap` / unload workflow
- log locations

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py -q`
Expected: FAIL because the doc does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create a concise runbook with these sections:
- prerequisites
- one-time setup
- choose microphone
- validate with dry-run
- install/load capture service
- stop/restart service
- check status and logs
- common mistakes

Keep the wording teammate-readable and short.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_docs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/operator-setup.md tests/test_docs.py
git commit -m "docs: add operator setup runbook"
```

### Task 6: Update README pointers

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the failing test expectation**

Extend `tests/test_docs.py` so README must reference:
- `docs/operator-setup.md`
- `deploy/launchd/com.ambient-memory.capture-agent.plist`

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_docs.py -q`
Expected: FAIL because README only points to smoke test and worker/API templates.

- [ ] **Step 3: Write minimal implementation**

Add short pointers under `Ops` for:
- operator setup
- capture-agent launchd template

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_docs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_docs.py
git commit -m "docs: link operator setup from readme"
```

## Chunk 3: Verification

### Task 7: Verify the full operator setup slice

**Files:**
- Verify only

- [ ] **Step 1: Run targeted docs verification**

Run: `uv run pytest tests/test_docs.py -q`
Expected: PASS

- [ ] **Step 2: Run shell syntax verification**

Run: `zsh -n scripts/start-capture-agent.sh`
Expected: PASS

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 4: Inspect git diff**

Run: `git --no-pager diff --color=never`
Expected: only the planned docs/template/wrapper changes are present.

- [ ] **Step 5: Commit the final batch if needed**

```bash
git add README.md .env.example docs/operator-setup.md deploy/launchd/com.ambient-memory.capture-agent.plist scripts/start-capture-agent.sh tests/test_docs.py
git commit -m "docs: add teammate capture agent setup flow"
```
