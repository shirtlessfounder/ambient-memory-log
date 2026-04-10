# Room Window Speech Gate Design

**Date:** 2026-04-09

**Goal:** reduce AssemblyAI room-path cost by skipping stitched `room-1` `600s` windows that do not contain enough actual speech to justify a naming/transcription request.

## Scope

This slice changes only the worker-side `room-1` delayed Assembly path:

- keep `30s` raw room capture unchanged
- keep the existing chunk-level silence filter unchanged
- add a second gate on stitched `600s` room windows before the Assembly call
- permanently skip low-speech room windows instead of retrying them

This slice does not change:

- teammate capture
- room capture timing
- S3 upload shape
- database schema
- non-room processing path
- room publish gating logic after the Assembly call

## Problem

The current delayed `room-1` path can still send low-value room windows to AssemblyAI even when they contain little or no meaningful speech.

Why:

- chunk-level silence filtering already removes obviously silent `30s` chunks
- but a stitched `600s` room window can still pass through with:
  - quiet background noise
  - a few non-silent chunks
  - tiny fragments of speech that are not worth a full Assembly request

This means hidden room batches can still incur Assembly cost even when they never produce useful output.

## Recommended Approach

Add a worker-side room-window speech gate right before the AssemblyAI call.

Flow:

1. keep the existing `30s` chunk silence filter
2. build the stitched `600s` room window as today
3. measure approximate speech-active duration on that stitched audio
4. if speech duration is below a minimum threshold, skip the window permanently
5. only call AssemblyAI when the window contains enough speech

## Alternatives Considered

### 1. Move all silence filtering from `30s` capture to `600s` worker windows

Pros:

- fewer filtering layers

Cons:

- uploads/stores much more dead air again
- increases backlog and S3 churn
- removes a capture-side optimization that already works

Rejected because it would undo existing savings.

### 2. Add a simple max-volume gate to the stitched room window

Pros:

- easy to implement

Cons:

- loud background noise or bumps can pass it
- does not distinguish meaningful speech from non-speech noise well enough

Rejected because it is too weak for the room-window decision.

### 3. Run a preview STT step before the full Assembly request

Pros:

- better signal on whether a window contains real speech

Cons:

- adds a second transcription path
- increases complexity and cost
- conflicts with the goal of reducing moving pieces

Rejected as unnecessary for the first cost-control slice.

## Design

### Runtime model

The room path becomes:

1. capture still uploads `30s` chunks
2. worker still batches `room-1` into stitched `600s` windows
3. worker runs a local speech-duration check on the stitched audio
4. if speech is below threshold:
   - skip the room window
   - mark those chunks done
   - never call AssemblyAI
5. if speech meets threshold:
   - continue into the existing delayed Assembly naming flow

### Threshold model

Add a worker config:

- `ROOM_MIN_SPEECH_SECONDS=20`

Meaning:

- `< 20s` of speech-like activity across the stitched `600s` window:
  - skip permanently
- `>= 20s`:
  - send to AssemblyAI

This threshold is intentionally conservative:

- low enough to keep real conversation
- high enough to skip dead-air windows with only tiny stray sounds

### Skip behavior

For low-speech windows:

- do not call AssemblyAI
- do not write transcript candidates
- do not write canonical utterances
- do mark the underlying room chunks complete so they do not retry forever
- do log the skip with:
  - room window start/end
  - chunk ids or chunk count
  - measured speech seconds
  - threshold

This is a terminal skip, not a retryable state.

### Implementation shape

Add a small worker-side speech analyzer helper that can be injected in tests.

Preferred behavior:

- input: stitched room audio bytes
- output: approximate speech-active seconds as `float`

Implementation can use local `ffmpeg` audio filtering heuristics as long as:

- it stays lightweight
- it is deterministic enough for operational tuning
- tests do not shell out directly

### File Map

- Modify: `src/ambient_memory/config.py`
- Modify: `src/ambient_memory/pipeline/worker.py`
- Create or modify: worker-side room audio analysis helper under `src/ambient_memory/pipeline/`
- Modify: `tests/test_worker_config.py`
- Modify: `tests/pipeline/test_worker.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ops-machine-setup.md`
- Modify: `tests/test_docs.py`

## Testing

### Worker/config

Verify:

- `ROOM_MIN_SPEECH_SECONDS` loads from worker config
- low-speech room windows do not call AssemblyAI
- low-speech room windows do not publish transcript/canonical rows
- low-speech room windows are marked complete and do not retry
- high-speech room windows still go through the current delayed room path

### Live verification

After rollout on Dylan’s machine:

- quiet `room-1` periods should stop generating Assembly usage
- spoken room windows should still go through delayed Assembly processing
- room naming behavior after the gate remains unchanged

## Success Criteria

- obvious low-value room windows stop hitting AssemblyAI
- quiet periods still upload raw chunks normally
- delayed named room flow still works on real spoken windows
- Assembly usage drops for quiet room stretches

## Risks

- speech-duration heuristics may undercount quiet speech if tuned too aggressively
- once a low-speech window is skipped, it is intentionally not retried
- the best threshold may need a small amount of live tuning
