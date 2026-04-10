# Room Second-Pass Enrichment Design

**Date:** 2026-04-10

**Goal:** improve `room-1` transcript usefulness and speaker labeling without changing teammate behavior, without depending on more hardware, and without overwriting the raw first-pass transcript path.

## Scope

This slice adds a delayed second-pass enrichment path on top of the existing stored `room-1` data:

- keep raw `30s` room capture unchanged
- keep current `600s` AssemblyAI room windows unchanged
- keep first-pass transcript storage unchanged
- add a separate enrichment layer for:
  - cleaned transcript text
  - resolved speaker identity
- start with a bounded run-once evaluation over the most recent `4h` of `room-1`

This slice does not change:

- teammate capture flow
- room microphone placement
- first-pass ASR vendor choice
- raw/canonical row deletion behavior
- utterance timing boundaries
- merge/split behavior of existing utterances

## Problem

Current live evidence shows the room path is no longer a total transcript failure, but identity remains weak.

Observed state from the live database on 2026-04-10:

- `room-1` text is often readable enough to follow the conversation
- the main failure is speaker naming, not total transcript collapse
- over the recent `2h`, `room-1` produced `292` canonical utterances:
  - `15` real-name labels
  - `249` fallback `A/B/C/D`
  - `28` null speaker labels

So the next highest-leverage move is not more teammate burden or a full hardware redesign. It is a second-pass inference layer that operates on already-stored transcript context.

## Recommended Approach

Add a delayed enrichment pipeline that reads already-stored `room-1` canonical utterances in `10-20 min` windows and writes a separate inferred result set.

The enrichment pipeline should do two jobs:

1. resolve speaker identity from `A/B/C/D/null` toward the real roster when evidence is strong enough
2. clean up obviously broken ASR phrasing while preserving the original raw text

Key constraints:

- never overwrite the raw first-pass text
- never overwrite the original `speaker_name`
- never invent, merge, split, or delete utterance rows in v1
- allow `unknown` when the model is not confident

## Alternatives Considered

### 1. Replace the primary room transcription stack again

Pros:

- conceptually simpler if it worked

Cons:

- highest blast radius
- does not address the underlying mixed far-field stream constraint
- weak evidence that another single first-pass swap alone will solve both transcript cleanup and identity

Rejected as the next step. Too much churn for too little certainty.

### 2. Add more hardware before fixing software

Pros:

- can improve source audio

Cons:

- does not guarantee better stored labels
- adds operational complexity
- conflicts with the goal that teammates should do nothing except talk

Rejected as the immediate next step. Hardware can still be revisited later.

### 3. Run one giant LLM prompt that rewrites and relabels everything at once

Pros:

- simpler to describe

Cons:

- worse debuggability
- harder to tell whether failures came from relabeling or rewriting
- harder to evaluate safely

Rejected for v1. The two inference jobs should stay separable.

## Design

### Data model

Keep the current first-pass tables as-is.

Add a separate enrichment table keyed to canonical utterance id.

Recommended fields:

- `canonical_utterance_id`
- `resolver_vendor`
- `resolver_version`
- `resolved_speaker_name`
- `resolved_speaker_confidence`
- `cleaned_text`
- `cleaned_text_confidence`
- `resolution_notes`
- `created_at`

Why a separate table instead of new columns on `aa_canonical_utterances`:

- raw vs inferred stays unambiguous
- easier rollback
- easier reruns with better prompts/models later
- easier A/B comparison across resolver versions

### Processing model

Input unit:

- stored `room-1` canonical utterances grouped into a `10-20 min` window

Context sent to the resolver:

- utterance id
- started/ended time
- raw text
- current `speaker_name` (`Dylan`, `A`, `B`, null, etc.)
- nearby already-named turns
- fixed room roster:
  - `Dylan`
  - `Niyant`
  - `Alex`
  - `Jakub`

The resolver must return per-utterance outputs only.

It must not:

- create new utterances
- delete utterances
- merge utterances
- split utterances
- move utterances in time

### Two-step enrichment flow

Use one enrichment worker with two internal passes per window.

Step 1: speaker resolution

- reason over the whole window
- map `A/B/C/D/null` to a real roster person when evidence is strong enough
- otherwise return `unknown`

Step 2: text cleanup

- clean each utterance using:
  - raw text
  - surrounding transcript context
  - the resolved speaker from step 1
- keep the cleaned text close to what was likely said
- do not stylize, summarize, or compress

Why this order:

- speaker identity can help text repair
- separating the steps makes debugging easier
- bad cleanup will not be confused with bad relabeling

### Rollout model

Start with a run-once command, not a daemon.

Initial run scope:

- only `room-1`
- only the most recent `4h`

Reason:

- fast quality read on real data
- bounded cost
- no background-job complexity yet

If quality is materially better, the same logic can later become a delayed continuous worker.

### Runtime and trust model

The inferred layer should be treated as enrichment, not truth.

That means:

- raw first-pass transcript remains the source of record
- inferred speaker name is a best-effort label
- cleaned text is a readability improvement, not courtroom-grade evidence

## File Map

Expected implementation area:

- Modify: `src/ambient_memory/models.py`
- Modify: migration path for new enrichment table
- Create: enrichment pipeline module under `src/ambient_memory/pipeline/`
- Create: resolver client/integration module
- Modify: CLI or operator command entrypoint for run-once backfill
- Modify: tests covering schema, resolver pipeline, and persistence
- Modify: operator docs once implementation exists

## Testing

### Unit/integration

Verify:

- enrichment rows are written separately from canonical rows
- original `speaker_name` is never overwritten
- original `text` is never overwritten
- resolver can mark rows as `unknown`
- the pipeline is row-preserving:
  - same utterance ids in
  - same utterance ids enriched out

### Evaluation

For the bounded `4h` run:

- compare raw vs enriched speaker labels
- compare raw vs cleaned text
- inspect concrete bad examples
- measure:
  - percentage of rows resolved to real names
  - percentage still `unknown`
  - percentage left unchanged

## Success Criteria

- enrichment materially increases usable real-name labeling on recent `room-1` data
- enrichment improves readability of obvious ASR failures without destroying the original text
- raw and inferred layers remain clearly separated
- the bounded `4h` run gives enough signal to decide whether a continuous enrichment worker is worth adding

## Risks

- the model can still make plausible-but-wrong guesses
- text cleanup cannot recover words that the mic never captured
- roster resolution may stay weak when multiple voices sound similar or topic cues are thin
- if prompts are too aggressive, cleanup can drift from the source wording
