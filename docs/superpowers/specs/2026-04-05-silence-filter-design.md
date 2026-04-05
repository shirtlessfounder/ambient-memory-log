# Silence Filter Design

**Date:** 2026-04-05

**Goal:** reduce waste from all-day always-on recording by dropping obviously silent 30-second chunks locally before upload, without changing the existing capture, worker, or dedup architecture.

## Scope

This slice adds a conservative silence filter in the local capture/upload path:

- inspect each completed chunk before upload
- skip obviously silent chunks
- keep non-silent chunks flowing through the current upload path unchanged
- add minimal config for enabling/disabling the filter and tuning the threshold
- document the behavior

This slice does not:

- trim speech within chunks
- split 30-second chunks into smaller pieces
- change worker logic
- change database schema
- add ML/VAD

## Problem

The current system records fixed 30-second WAV chunks throughout the active window. That works functionally, but for real all-day operation most chunks are expected to contain no speech at all.

Today those silent chunks still incur:

- local disk writes
- S3 uploads
- worker scans
- transcription requests
- storage and operational noise

The system tolerates silence, but it does not optimize for it.

## Recommended Approach

Add a conservative local silence check in the uploader path and drop only clearly silent chunks before they ever reach S3.

Why this is the right fit:

- largest operational win for the smallest code change
- preserves the rest of the pipeline unchanged
- works across teammate and room-mic roles
- avoids premature complexity like in-chunk trimming or ML-based VAD

## Alternatives Considered

### 1. Worker-side discard of silent chunks

Pros:

- simpler to reason about

Cons:

- still uploads and stores empty chunks
- still burns worker/transcription effort

Rejected because it does not solve the real cost/noise problem.

### 2. Aggressive VAD / trim within chunks

Pros:

- better long-term efficiency

Cons:

- much higher implementation and tuning risk
- easier to accidentally cut quiet speech
- changes chunk semantics more deeply

Deferred. It is not needed for the first iteration.

### 3. Local conservative silence drop

Pros:

- best immediate ops win
- small surface area
- low architectural risk

Cons:

- threshold tuning matters, especially for far-field room audio

Recommended.

## Design

### Runtime model

When the uploader sees a chunk ready for upload:

1. inspect the local WAV file
2. determine whether it is obviously silent
3. if silent:
   - do not upload
   - do not register a DB row
   - remove/mark-complete locally so it does not retry forever
   - log a skip event
4. if not silent:
   - keep the current upload path unchanged

### Detection model

Use local `ffmpeg`-based audio level analysis rather than ML/VAD.

Recommended v1 rule:

- measure the chunk’s `max_volume`
- skip only if it never rises above a conservative threshold

This is intentionally blunt:

- catches dead air and near-dead air
- keeps low-volume real speech safer
- avoids tuning multiple metrics at once

### Configuration model

Keep v1 small and explicit:

- `SILENCE_FILTER_ENABLED`
- `SILENCE_MAX_VOLUME_DB`

Recommended defaults:

- enabled by default for capture roles
- threshold conservative enough to avoid dropping quiet speech

### Logging model

When a chunk is skipped, log:

- source id
- filename
- measured max volume
- threshold

This is important for threshold tuning on room-mic audio.

## File Map

- Modify: `src/ambient_memory/capture/uploader.py`
- Modify: `src/ambient_memory/config.py`
- Modify: `.env.example`
- Modify: `docs/teammate-setup.md`
- Modify: `docs/ops-machine-setup.md`
- Add or modify focused capture tests

## Testing

This slice should be verified with:

- unit tests proving obviously silent chunks are skipped
- unit tests proving non-silent chunks still upload normally
- config tests for the new silence settings
- full `uv run pytest -q`

Manual validation target:

- a silent or nearly silent chunk gets skipped locally
- a spoken room-mic chunk still uploads and transcribes

## Risks

- threshold too aggressive could drop quiet real speech
- room-mic far-field capture is the main sensitivity case
- local analysis adds a little CPU cost, but much less than uploading/transcribing silence

## Non-Goals

- speech trimming within a chunk
- semantic VAD
- worker/API changes
- schema changes
