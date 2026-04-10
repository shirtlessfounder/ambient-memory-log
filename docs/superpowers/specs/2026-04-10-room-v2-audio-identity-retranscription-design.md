# Room V2 Audio Identity And Retranscription Design

**Date:** 2026-04-10

**Goal:** deliver a step-function improvement on `room-1` by moving speaker identity onto audio-track verification and replacing light text cleanup with a stronger audio-aware retranscription layer, while keeping the current raw capture and raw first-pass storage intact.

## Scope

This slice changes only the inferred `room-1` layer.

Keep as-is:

- Owl room capture
- `30s` raw chunk upload
- current `600s` room batching in the worker
- current AssemblyAI first-pass room transcript storage
- raw canonical utterances already stored in the database

Change:

- stop treating the current text-only OpenAI relabeler as the source of speaker truth
- add audio-track identity verification per `15m` room window
- add a second-pass audio-aware retranscription layer
- store inferred identity and inferred transcript separately from raw canonical rows

This slice does not change:

- teammate behavior
- teammate device setup
- raw storage retention
- search/API product semantics yet
- non-room sources

## Problem

The current room system has two different failure modes:

1. the always-running room pipeline stores only fallback diarization labels most of the time
2. the new text-only enrichment can make confident but wrong real-name guesses

Current live evidence from the last `4h` on `room-1`:

- `810 / 810` canonical utterances used fallback labels only
- `0 / 810` had real teammate names from the live room path
- active `15m` room windows varied from `10` utterances to `176` utterances
- active windows usually carried all four fallback tracks `A/B/C/D`

Current text-only enrichment evidence:

- it can improve readability somewhat
- it can also confidently assign the wrong teammate to an external speaker
- over the recent `4h`, `6.5%` of raw diarization-track windows mapped one raw track to multiple resolved names inside the same `15m` window

That is the critical structural problem:

- transcript semantics are not a reliable source of speaker identity in this room
- especially under mixed in-room conversation, short turns, interruptions, and remote/external participants

## Recommended Approach

Build room v2 around two explicit inferred layers:

1. `audio-track identity`
2. `audio-aware retranscription`

Use the current AssemblyAI room path only as the raw layer and the source of initial diarization tracks.

The new identity path should:

- use Assembly's `A/B/C/D` diarization tracks only as grouping keys
- reconstruct audio for each track inside a fixed `15m` window
- verify each track against enrolled teammate voiceprints
- assign one identity per track for the whole window

The new transcript path should:

- run a stronger second-pass transcription over room audio
- use audio, not text rewriting, as the source of transcript improvement
- store the inferred transcript separately from the raw Assembly transcript

## Variability Requirements

The design must explicitly handle current room variability:

- some windows are dense (`176` utterances in `15m`)
- some windows are sparse (`10` utterances or fewer)
- some windows have all `A/B/C/D` active
- some windows have only one or two active tracks
- external or remote speakers can appear in the same room audio
- some tracks are mostly short acknowledgements or fragments

So v2 must not assume:

- exactly four real people in the room
- every active track belongs to a teammate
- every track has enough speech for reliable teammate matching
- transcript text alone can identify speakers

## Alternatives Considered

### 1. Keep the current text-only OpenAI relabeler and just improve the prompt

Pros:

- lowest implementation effort

Cons:

- attacks the symptom, not the cause
- still relies on transcript semantics instead of speaker audio
- already proven capable of confident false teammate labels

Rejected because it will not produce a step-function improvement.

### 2. Replace AssemblyAI entirely

Pros:

- fewer vendors if it worked

Cons:

- high blast radius
- throws away the current room batching/transcript pipeline before isolating the actual failure
- does not guarantee better identity

Rejected as premature.

### 3. Reuse the old pyannote room strategy unchanged

Pros:

- existing code and voiceprints already exist

Cons:

- previous attempts worked on short mixed audio, not stable per-track bundles
- would likely repeat the old failure mode if copied directly

Rejected as-is.

### 4. Add more hardware before fixing inference

Pros:

- may improve source audio

Cons:

- does not guarantee better DB labels
- adds operational complexity before fixing the inference architecture

Rejected as the immediate next step.

## Design

### Raw vs inferred contract

Keep raw room storage unchanged:

- raw Assembly transcript text remains stored as-is
- raw Assembly diarization labels remain stored as-is

Add v2 inferred outputs separately:

- inferred speaker identity
- inferred transcript text
- audit data showing how the inferred identity was chosen

The raw layer remains the source of record.

### Identity model

Allowed inferred speaker outputs for room v2:

- `Dylan`
- `Niyant`
- `Alex`
- `Jakub`
- `external-1`
- `unknown`

Rules:

- one diarization track maps to one inferred identity within a `15m` window
- teammate names are used only when audio verification is strong enough
- a coherent non-teammate track becomes `external-1`
- weak evidence becomes `unknown`

This is intentionally conservative.

### Track-level audio identity

For each `15m` room window:

1. load room utterances and their time spans
2. group them by Assembly diarization track `A/B/C/D`
3. extract and stitch audio for each track into one track bundle
4. measure usable speech duration for each track bundle
5. if a track does not have enough speech, mark it `unknown`
6. otherwise verify that track bundle against enrolled teammate voiceprints
7. assign the track identity using thresholds and margin rules

Recommended thresholds:

- require a minimum pooled speech duration before attempting teammate assignment
- require top-match confidence to clear a threshold
- require top-match minus second-best margin to clear a threshold
- otherwise do not assign a teammate name

This design is specifically meant to handle:

- short backchannels
- partial overlaps
- remote guest speech
- noisy room fragments

### Identity backend recommendation

Initial identity backend:

- `pyannote` voiceprint matching on stitched per-track bundles

Why start here:

- existing teammate voiceprints already exist
- existing integration code already exists
- existing failure was on short mixed segments, not on stable per-track bundles

Important design constraint:

- the verifier must be isolated behind a room-track identity interface
- if pyannote remains weak on stitched track bundles, swap the verifier without changing the rest of the room v2 pipeline

### Audio-aware retranscription

The current `cleaned_text` style pass is not enough.

Replace it with a true second-pass transcription path:

- use room audio, not just stored text
- use more context than per-utterance text cleanup
- store the resulting inferred transcript separately

Initial retranscription backend recommendation:

- OpenAI `gpt-4o-transcribe-diarize` or `gpt-4o-transcribe`, with the preference to start with the diarized transcription path so the inferred transcript is generated from full window audio rather than from isolated text edits

Rationale:

- official OpenAI audio docs currently describe `gpt-4o-transcribe` as a more accurate speech-to-text model than older Whisper models, and `gpt-4o-transcribe-diarize` as the diarized transcription variant available in the transcription API
- this gives a real audio-aware second pass instead of surface-level punctuation repair

Current sources:

- https://platform.openai.com/docs/models/gpt-4o-transcribe
- https://platform.openai.com/docs/models/gpt-4o-transcribe-diarize

### Transcript alignment model

Use AssemblyAI as the raw segmentation baseline.

For v2 inferred transcript:

- retranscribe the full `15m` window audio
- align inferred transcript segments back onto the existing room utterance timeline by time overlap
- then apply the track-level inferred identity to those aligned utterances

Why this approach:

- preserves comparability against the existing raw rows
- avoids exploding API calls to one request per utterance
- keeps the v2 inferred layer auditable against the current room timeline

If alignment proves too unstable in live evaluation, fallback option:

- retranscribe shorter contiguous same-track blocks instead of the full window

That is a secondary fallback, not the preferred first version.

### Storage model

Reuse the current utterance enrichment concept, but extend it to carry v2 auditability.

Recommended additional stored fields:

- `identity_method`
- `identity_track_label`
- `identity_window_started_at`
- `identity_match_label`
- `identity_match_confidence`
- `identity_second_match_label`
- `identity_second_match_confidence`
- `transcript_method`
- `transcript_confidence`

Existing inferred fields remain useful:

- `resolved_speaker_name`
- `resolved_speaker_confidence`
- `cleaned_text`

For v2, `cleaned_text` becomes the inferred second-pass transcript text, not just a punctuation cleanup.

### What gets demoted from v1

The current OpenAI text-only relabeler should no longer be the source of speaker identity.

It may still remain useful for:

- a light final polish pass on inferred transcript text
- optional explanation notes

But it should not decide teammate names.

## Runtime flow

Room v2 for one `15m` window:

1. current worker stores raw Assembly room utterances as today
2. v2 run loads that raw window
3. group utterances by Assembly diarization track
4. reconstruct track audio bundles
5. verify each track bundle against teammate voiceprints
6. assign one identity per track:
   - teammate
   - `external-1`
   - `unknown`
7. retranscribe the full window audio with the second-pass transcription backend
8. align inferred transcript segments back to the raw utterance timeline
9. write inferred transcript text + inferred speaker identity + audit fields

## File Map

Expected implementation areas:

- Modify: `src/ambient_memory/models.py`
- Modify: migration path for enrichment/audit fields
- Create: room-track audio extraction module under `src/ambient_memory/pipeline/`
- Create: room-track identity verifier module under `src/ambient_memory/pipeline/` or `src/ambient_memory/integrations/`
- Modify: `src/ambient_memory/integrations/pyannote_client.py` only if needed for track-bundle verification support
- Create: second-pass audio retranscription integration module
- Modify: `src/ambient_memory/pipeline/room_enrichment.py`
- Modify: `src/ambient_memory/cli.py`
- Modify: tests for model, pipeline, integration, CLI, and docs

## Testing

### Unit / integration

Verify:

- track audio extraction preserves track grouping
- track identity is stable within a window
- teammate assignment requires threshold + margin
- low-speech tracks become `unknown`
- external coherent tracks become `external-1`
- inferred transcript rows are stored separately from raw text
- raw canonical fields remain unchanged
- reruns remain idempotent by version

### Evaluation

Run on a bounded recent room window first.

Measure:

- percentage of room rows still using only raw fallback labels
- percentage of v2 inferred rows with real teammate names
- percentage of v2 inferred rows marked `external-1`
- percentage marked `unknown`
- transcript readability improvement on sampled windows
- concrete false-teammate-label rate on human spot checks

### Ground-truth review

A window should be considered a failure if:

- an external/remote participant is confidently labeled as a teammate
- one raw diarization track maps to multiple inferred identities inside the same window without a justified diarization change
- inferred transcript drifts materially from what was likely said

## Success Criteria

- room identity improves materially over both:
  - raw Assembly `A/B/C/D`
  - current text-only OpenAI relabeling
- external/remote speakers stop being confidently mislabeled as teammates
- transcript quality improves through audio-aware retranscription, not just punctuation edits
- raw and inferred layers remain separate and auditable

## Risks

- Assembly diarization itself can still be imperfect, so bad raw track grouping can poison downstream identity
- pyannote may still underperform if room track bundles remain too noisy or too short
- full-window retranscription alignment may be messy on heavily overlapping speech
- the best verifier backend may still need to change after live evaluation
