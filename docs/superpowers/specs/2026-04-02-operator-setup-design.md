# Operator Setup Design

**Date:** 2026-04-02

**Goal:** make the ambient memory MVP easy for teammates to install and run on their MacBooks without editing XML or memorizing service commands.

## Scope

This slice adds operator-facing setup assets for the existing capture/worker/API system:

- one generic capture-agent `launchd` template
- one small wrapper script that starts the capture agent using repo-local configuration
- one teammate-facing setup document covering install, config, start, stop, status, and logs
- small README pointers so the new flow is discoverable

This slice does not change the capture pipeline architecture, add a native Mac app, or introduce multi-host orchestration.

## Problem

The current MVP already supports:

- a per-laptop capture agent
- a shared worker
- a shared API

But the repo only ships long-running service templates for the worker and API. Teammates would currently need to:

- infer how to keep the capture agent running in background
- edit shell state or plist XML directly
- discover logs and lifecycle commands themselves

That is too brittle for a four-person living-room deployment.

## Recommended Approach

Use one generic capture-agent plist plus one wrapper script, with teammate-specific values stored only in `.env`.

Why this is the right fit:

- lowest teammate friction
- avoids hand-editing XML
- matches the existing CLI/runtime model
- keeps one source of truth for per-laptop settings
- easy to debug because the wrapper can log explicit startup failures

## Alternatives Considered

### 1. Generic plist with all teammate-specific values embedded

Pros:

- no wrapper script

Cons:

- every teammate edits XML
- higher breakage risk
- duplicates config already modeled in `.env`

Rejected because operator ergonomics are worse for no meaningful system benefit.

### 2. Four prefilled plist templates (`desk-a`, `desk-b`, `desk-c`, `desk-d`)

Pros:

- slightly less initial thinking for the first four laptops

Cons:

- duplicates nearly identical files
- bakes team-specific assumptions into repo assets
- awkward if a laptop is reassigned or renamed

Rejected because the system is the same on every laptop and should stay generic.

### 3. Full bootstrap utility that generates plist/config automatically

Pros:

- best eventual UX

Cons:

- more code, validation, and test surface than needed for the next step

Deferred. The generic-template approach is enough for MVP operations.

## Design

### Operator model

Each MacBook teammate does the following once:

1. clone repo
2. install dependencies (`uv`, `ffmpeg`)
3. copy `.env.example` to `.env`
4. set laptop-local values such as `SOURCE_ID`, `DEVICE_OWNER`, `SPOOL_DIR`, and capture device name
5. run one dry-run validation command
6. load one generic capture-agent plist into `launchd`

After that, the capture agent starts in background on login.

### Configuration model

The only teammate-edited file should be `.env`.

Expected per-laptop values:

- `SOURCE_ID`
- `SOURCE_TYPE=macbook`
- `DEVICE_OWNER`
- `SPOOL_DIR`
- `AWS_REGION`
- `S3_BUCKET`
- `DATABASE_URL`
- `DATABASE_SSL_ROOT_CERT`

The wrapper script should also support a dedicated variable for the selected mic, so operators do not need to remember `--device` flags. Recommended name: `CAPTURE_DEVICE_NAME`.

### Service startup model

The new wrapper script is responsible for:

- changing into repo root
- validating that `.env` exists
- sourcing `.env` into the process environment
- checking that the capture device variable is set
- executing `uv run ambient-memory agent run --device "$CAPTURE_DEVICE_NAME"`

The generic plist should:

- invoke the wrapper script rather than inlining the whole command
- set `WorkingDirectory`
- set log paths
- run at load
- keep the agent alive

### Documentation model

The new teammate-facing setup doc should be a practical runbook, not an architecture note.

It should cover:

- prerequisites
- repo clone + dependency install
- `.env` setup
- device discovery and dry-run validation
- loading/unloading the capture plist
- checking `launchctl` status
- tailing stdout/stderr logs
- common failures: missing `ffmpeg`, wrong device name, missing `.env`, login-item/service not loaded

README should point to this doc and to the new capture-agent plist.

## Files

Planned file map:

- Create: `docs/operator-setup.md`
- Create: `deploy/launchd/com.ambient-memory.capture-agent.plist`
- Create: `scripts/start-capture-agent.sh`
- Modify: `README.md`
- Modify: `.env.example`
- Create or modify tests for docs/templates if needed, likely `tests/test_docs.py`

## Testing

This slice should be verified with:

- targeted tests for docs/template presence and key strings
- a CLI or wrapper smoke check where practical
- full `uv run pytest -q`

Manual operator validation target:

- teammate can bring up one laptop with only `.env` edits plus `launchctl load`
- no plist XML edits required

## Risks

- `.env` parsing from shell is less robust than Pydantic parsing inside Python; wrapper should keep parsing simple and fail loudly on missing values
- device names can vary slightly across machines; docs must make `ambient-memory list-devices` and dry-run mandatory
- `launchd` path assumptions (`uv`, repo root, log directory) must remain explicit in the template

## Non-Goals

- shipping a native macOS app
- generating per-user plists automatically
- central fleet management
- changing worker/API deployment model

