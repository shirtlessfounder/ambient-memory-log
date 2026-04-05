# Dual Capture Design

**Date:** 2026-04-05

**Goal:** make it easy for any Mac to run both the teammate mic and room mic capture roles together through one command and one `launchd` service, without changing the existing capture/worker/dedup architecture.

## Scope

This slice adds generic optional dual-capture orchestration on top of the existing role-based commands:

- one new `ambient-memory start-dual-capture` command
- one wrapper script for dual-capture `launchd` startup
- one `launchd` plist for always-on dual capture
- docs that explain how a Mac can run teammate-only, room-only, or dual capture

This slice does not change:

- audio chunk format
- worker logic
- dedup logic
- API logic
- one-source capture behavior

## Problem

The current repo now has the right building blocks:

- `start-teammate`
- `start-room-mic`
- `start-worker`
- `start-api`
- role-specific env files

We also manually proved one Mac can run:

- built-in MacBook mic capture
- room mic capture
- worker processing

at the same time.

But the current operator path is still awkward for dual capture because it requires:

- two separate terminals for manual runs
- two separate mental models for startup
- no single always-on service for the “both mics on one Mac” mode

That is friction we can remove without changing the underlying system.

## Recommended Approach

Add one orchestration command, `ambient-memory start-dual-capture`, that starts and supervises two child capture processes:

- `ambient-memory start-teammate`
- `ambient-memory start-room-mic`

Why this is the right fit:

- preserves the already-proven one-source-per-process model
- avoids building a new multi-device capture engine
- gives humans the one-command UX they want
- supports both manual use and `launchd`
- keeps teammate-only and room-only modes unchanged

## Alternatives Considered

### 1. One monolithic multi-device capture process

Pros:

- one process
- one place to manage devices

Cons:

- much higher implementation risk
- harder to debug
- changes the capture architecture for no proven need

Rejected because the current isolated-process model already works.

### 2. Keep two separate commands and only document “run both”

Pros:

- lowest code change

Cons:

- not the UX the operator wants
- no single `launchd` service for dual mode
- still feels bespoke

Rejected because the manual orchestration friction is exactly what this slice should remove.

### 3. Shell-only orchestration outside the CLI

Pros:

- simple to hack together

Cons:

- splits startup behavior between shell scripts and CLI
- harder to test cleanly
- weaker discoverability in `ambient-memory --help`

Rejected in favor of a CLI-first orchestration command with a thin shell wrapper for `launchd`.

## Design

### Runtime model

`start-dual-capture` should:

1. validate that `.env.teammate` and `.env.room-mic` both exist
2. start one child process for teammate capture
3. start one child process for room-mic capture
4. stream child stdout/stderr with clear role prefixes
5. wait until interrupted or until one child exits unexpectedly
6. on shutdown, stop both children cleanly

### Failure model

If one child fails to start or exits unexpectedly:

- log which role failed
- terminate the sibling process
- exit non-zero

This avoids silently running only one mic when the operator thought dual capture was active.

### CLI model

New public command:

- `uv run ambient-memory start-dual-capture`

The existing commands remain first-class primitives:

- `start-teammate`
- `start-room-mic`

Dual capture is orchestration, not a new source type.

### Service model

Add:

- `scripts/start-dual-capture.sh`
- `deploy/launchd/com.ambient-memory.dual-capture.plist`

This service should run:

- `uv run ambient-memory start-dual-capture`

The current teammate capture plist should stay for single-role setups.

### Documentation model

Docs should describe three supported capture modes:

1. teammate-only
2. room-mic-only
3. dual capture on one Mac

The teammate doc should stay simple and focused on one-person laptops.

The ops-machine doc should explain the dual-capture option and how it fits alongside worker/API.

README should point to the new dual-capture entry points.

## File Map

- Create: `deploy/launchd/com.ambient-memory.dual-capture.plist`
- Create: `scripts/start-dual-capture.sh`
- Modify: `src/ambient_memory/cli.py`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_docs.py`

## Testing

This slice should be verified with:

- CLI tests for `start-dual-capture`
- docs/template tests for the new wrapper and plist
- full `uv run pytest -q`

Manual operator validation target:

- one Mac starts dual capture with one command
- both `desk-a` and `room-1` upload/process successfully
- shutdown interrupts both child capture processes cleanly

## Risks

- process supervision bugs could leave one child orphaned if shutdown handling is sloppy
- mixed child logs can become noisy; role prefixes are important
- if operators misunderstand dual capture as “one source”, they may be surprised to see two source ids downstream

## Non-Goals

- replacing the current capture engine with a multi-input engine
- changing chunk timing or worker windows
- automatic role inference
- silence/VAD optimization
