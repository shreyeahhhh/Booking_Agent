# Master Plan

The build order for the voice booking agent. Every step is a commit-sized unit with an
explicit acceptance test. Work top to bottom — later phases assume earlier ones are green.

**Deadline: 2026-09-12.** Submission is a GitHub URL + a live demo URL emailed to
`annmary2310@gmail.com`.

---

## Rules of engagement

1. **The deterministic core is built and tested before any AI is wired in.** If Phase 1
   is not green, nothing later can be trusted.
2. **A step is done when its acceptance criterion passes**, not when the code is written.
3. **Commit at every step boundary.** Small commits with real messages; the commit log is
   part of what a reviewer reads.
4. **Deploy on Day 4, before polish.** Deployment always surfaces problems; surface them
   on Thursday, not Sunday.
5. **If behind schedule, cut in this order:** Phase 5 golden-conversation harness → the
   `suggested_reply` escape hatch → barge-in → UI polish. **Never cut Phase 4 hardening.**
   A polished agent that crashes scores worse than a plain one that does not.
6. **Every design decision must be explainable in an interview.** If a piece of code
   cannot be defended aloud, it does not go in.

### Definition of done (applies to every step)

- `ruff check .` clean
- `pytest` green
- The step's own acceptance criterion demonstrated
- Committed with a message describing *why*, not just *what*

---

## Phase 0 — Foundation

**Goal:** a repository that runs, lints, tests and documents itself.

| # | Step | Status |
|---|---|---|
| 0.1 | Repo scaffold, architecture doc, pinned dependencies | ✅ `1d7619f` |
| 0.2 | Design docs: booking schema, LLM contract, conversation design, test plan | ✅ |
| 0.3 | Runnable skeleton: settings, FastAPI app, `/health`, import-boundary test | ✅ |
| 0.4 | Frontend scaffold: Vite + React + TS, dev proxy to the backend | ✅ |

**Acceptance:** `uvicorn app.main:app` serves `/health`; `npm run dev` serves a page that
calls it successfully; `pytest` green with at least one meaningful test.

---

## Phase 1 — Deterministic core (Day 1)

**Goal:** prove that out-of-order input, corrections, ambiguity and completeness all work
**with zero AI involved.** This phase is what separates the project from a prompt wrapper,
and it is the single highest-value day.

No module in this phase may import from `app/llm/` or `app/services/`.

| # | Step | Files | Acceptance |
|---|---|---|---|
| 1.1 | Booking state schema with field provenance | `domain/state.py` | ✅ `Field[T]` carries value, status, confidence, evidence, turn, revisions. Round-trips through JSON. |
| 1.2 | Declarative field specification table | `domain/specs.py` | ✅ One table drives completeness, question priority and answer types. Conditional requirements are predicates over state. |
| 1.3 | Value normalisers | `domain/normalizers.py` | ✅ "tomorrow", "this Saturday", "next Friday", "morning", "half past four", "a couple" all resolve. **"Saturday" said *on* a Saturday resolves to the coming Saturday, not today.** |
| 1.4 | Vehicle and helper inference | `domain/inference.py` | ✅ Item list → vehicle type + helper count via a lookup table. Pure function, no AI. Calibrated so the docs' own worked examples (sofa alone; sofa + 3 cupboards) produce exactly the vehicle/helper numbers already committed to in design.md. |
| 1.5 | Reducer with correction and conflict rules | `domain/reducer.py` | ✅ `op: set` against a `CONFIRMED` field is **rejected** as a conflict. `op: correct` replaces and pushes to `revisions[]`. Cascade invalidation resets derived fields, but never an explicitly user-confirmed one. |
| 1.6 | Completeness engine | `domain/completeness.py` | ✅ Pure fn → `missing[]`, `ambiguous[]`, `conflicts[]`. `AMBIGUOUS` counts as unfilled. |
| 1.7 | Question policy | `domain/policy.py` | ✅ Selects the highest-priority unfilled slot. **Reads state only, never history** — a filled slot is unaskable. Bounded clarification (give up after 2 attempts, record an assumption) included. |
| 1.8 | Unit test suite | `tests/unit/` | ✅ 107 tests. Includes the import-boundary test asserting `domain/` never imports `llm/` or `services/` (9 files checked, 0 violations). |

**Acceptance for the phase:** a scripted test feeds patches in scrambled order, applies
two corrections and one contradiction, and asserts the exact final state — with no network
calls and no API key set.

**Acceptance met** — see `tests/unit/test_phase1_acceptance.py`.

**Bugs the test suite caught before they could ship:**
- `Field[T]` never actually stored *why* something was ambiguous (the reducer computed
  a reason and threw it away) — added `Field.ambiguity: AmbiguityReason | None`.
- The first-pass vehicle/helper thresholds contradicted the docs' own worked examples
  (a bare sofa landed on a three-wheeler, not the Tata Ace design.md promises) —
  retuned against the examples already committed to the documentation.
- "A time phrase fills both the window and the exact time" was designed but never wired
  into the reducer — it only wrote to whichever single field the patch targeted. Fixed
  with a dedicated `_apply_time_patch` path.


---

## Phase 2 — Language understanding (Day 2)

**Goal:** a complete, correct text conversation. Voice is deliberately deferred.

| # | Step | Files | Acceptance |
|---|---|---|---|
| 2.1 | Structured output contract | `llm/schema.py` | ✅ Pydantic → JSON Schema accepted by Groq with `strict: true` (all props required, `additionalProperties: false`, optionals as `anyOf[T, null]`). Verified with a live call, not just by inspection. |
| 2.2 | Extractor prompt | `llm/prompts/extractor.md` | ✅ Prompt held as a file, not a string literal, so it can be diffed and reviewed. Field vocabulary generated from `FIELD_SPECS` (`llm/prompt_builder.py`), not hand-typed. Verified live: the real prompt turns 2.1's invented field names (`from_location`, `moving_date`) into genuine schema paths for the project's own canonical example. |
| 2.3 | Groq extractor client | `llm/extractor.py` | ✅ Timeout, one retry (transient errors and the observed schema-violation 400), and a repair pass that feeds the parse/validation error back to the model. Async client (`AsyncGroq`), matching phase 3's FastAPI routes. Compact state serialisation (non-empty fields only). Verified live: a two-turn conversation with a real correction produced the exact right `op: correct` with `previous_value` set. |
| 2.4 | Response templates | `conversation/templates.py` | ✅ `acknowledgment(what landed) + question(slot, state)` -- collapsed from the three-part design (a correction reads naturally as part of the acknowledgment itself). Deterministic variant rotation keyed on `state.turn`, no separate state needed. Verified by reading real generated output for a full conversation, not just unit assertions. |
| 2.5 | Conversation state machine | `conversation/machine.py` | ✅ Phase transitions derived from `policy.sweep_and_select`'s own `SlotReason` rather than re-computed independently. No separate CORRECTING phase -- a correction is just another turn through the same general recomputation, which correctly reopens `GATHERING` for a scope-expanding correction and stays at `REVIEW` for a simple one. Verified end to end: the bounded-clarification give-up (built in phase 1, never wired to anything) fires for the first time in a real run. |
| 2.6 | Deterministic summary renderer | `conversation/summary.py` | ✅ Final summary generated from state, **not** written by the model. Includes assumptions and correction history (only fields actually corrected, not a status report on every field). Structured `SummaryLine` list is the seam for a future spoken/condensed renderer (phase 3) to reuse rather than re-deriving from state a second time. |
| 2.7 | Text REPL harness | `tests/repl.py` | ✅ Type a conversation in the terminal, watch state fill. First time everything from 2.1-2.6 ran together against the real API. Caught the most significant bug this project has found (see below), then a complete, correct 7-turn booking ran end to end: locations, items, floors, a mid-conversation topic change, confirming the inferred vehicle, and confirming the final summary. |

**Acceptance:** a full booking completed by typing, with a correction mid-conversation and
a correction at the summary stage, producing a correct final summary.

**Step 2.1 found two things only a real call could surface, not schema inspection:**
- Groq's strict-mode validator rejected an `anyOf` branch that was a bare `$ref` next to
  `null` as "not disambiguated," even though the referenced type (an enum) is trivially
  distinguishable from `null` once resolved. The check appears to run before `$ref` is
  dereferenced. Fixed by inlining every `$ref` before sending the schema.
- Strict mode is not an absolute guarantee against a malformed generation: `gpt-oss-120b`
  occasionally violated its own schema (wrapping an enum value in an object) even at
  temperature 0. Groq validates server-side and returns a 400 rather than passing broken
  JSON through, which is the right failure mode — but `docs/architecture.md` and
  `docs/design.md` originally overstated this as an unconditional guarantee, sourced from
  Groq's own docs rather than verified against real behaviour. Corrected in both files.

**Step 2.2 found three gaps of the same shape — a schema field with no mechanism that ever
populates it — surfaced by writing out the extractor's field vocabulary and asking "does
anything actually set this?" for each one:**
- `booking_type` existed and got confirmed by `confirm_all`, and two predicates in
  `specs.py` (`_needs_items`, `_needs_packing_check`) read it — but nothing anywhere ever
  set it, so those predicates were permanently stuck at their default answer. Rekeyed both
  off `goods.category` instead, which is a required field the extractor reliably populates.
  `booking_type` itself is kept as a plain optional field for summary framing only.
- `schedule.is_asap` existed and got confirmed by `confirm_all`, but `schedule.time_window`
  was unconditionally required regardless of it — design.md already documented "satisfied by
  is_asap" as the intended rule, but nothing implemented it. A user saying "as soon as
  possible" would still have been asked what time of day they meant. Fixed by making
  `time_window` conditional on `not is_asap`.
- `notes` (the "any additional requirements" field — one of the PDF's own six summary
  categories) had no reducer support at all; any patch targeting it was silently dropped as
  an unresolvable field path. Fixed with a small dedicated append/clear handler, the same
  shape as the items-list handler but without item's name-matching complexity.

All three were confirmed with a failing repro before the fix and a regression test after,
the same discipline as every other bug this project has caught. `docs/design.md` and
`docs/test-plan.md` updated to match.

**Step 2.3 corrected a stale estimate with a real measurement.** `architecture.md`'s
original "~900 tokens" figure was a guess made before the extractor existed. A live call
measured **2654 prompt tokens / 371 completion tokens** for a realistic mid-conversation
turn — nearly 3x the guess, because the field vocabulary and worked example that made
step 2.2's prompt actually work (see its note above) are real content with a real token
cost. The latency budget table in `architecture.md` is recalculated from the corrected
completion-token count (~200 → ~370), which moves the LLM row from ~300-500ms to
~700-800ms and the total from ~1.5-2.5s to ~2-2.9s. Still not an end-to-end measurement —
that needs the full STT+LLM+TTS loop, which only exists once phase 3 is built.

The client's own logic (state serialisation, message building, parsing, and retry/repair/
fallback — transient errors, the observed schema-violation 400, a malformed-JSON repair
pass, the safe fallback, and — deliberately — a non-retriable error like a bad API key
propagating instead of being swallowed into a misleading "didn't catch that") is covered
by 17 mocked tests in `test_extractor.py`, so this reliability logic runs on every commit
at zero cost rather than only being exercised by the live tests.

**Step 2.4 found the most significant bug so far, purely by reading generated output for a
real conversation rather than trusting narrower unit tests:** the reducer built every new
`Field` via the bare, unparameterised `Field(...)` class. Pydantic has no type parameter to
validate or coerce against on the bare class, so an enum-typed value like `"furniture"` was
stored as a plain `str`, never as `GoodsCategory.FURNITURE`. This had been silently
harmless everywhere a `StrEnum`'s string-equality masked it — `specs.py`'s predicates,
dict lookups by string key — for the whole of phases 1 and 2, until `templates.py`'s
category-formatting code called `.value` expecting a real enum instance and crashed. Fixed
with `specs.field_class(path)`, which derives the correct parameterised class from the
Pydantic model annotations themselves (not a hand-maintained `{path: type}` table — one
more of those was exactly how this class of bug kept recurring all day). Regression tests
added in both `test_specs.py` and `test_reducer.py`.

Two more findings, also only visible by reading real output:
- `goods.category` was acknowledged redundantly alongside the actual item list ("...a sofa
  and 2 cupboards, furniture") — suppressed when items are already present in the same turn.
- `needs_disassembly`/`needs_packing` only had phrasing for a `True` answer; a `False`
  answer produced no acknowledgment at all, silently dropping the user's "no" from the
  response. Both directions now have phrasing.

Also split `format_date` into a short form ("Saturday") for spoken acknowledgments and a
full form ("Saturday, 12 September") reserved for the written summary (step 2.6) — the full
form read over-formal said aloud mid-sentence.

One test-only bug, caught by the test itself hanging for two minutes rather than silently
passing: an early version of the confirm-inferred test drove a while-loop off a hand-typed
filler dict that omitted `pickup.locality`, so the policy correctly kept asking for it
forever. Rewritten to fill every required field directly in two fixed passes — no loop,
and a wrong dict now fails an assertion in under a second instead of hanging.

**Step 2.5 closed a real wiring gap and caught two bugs in itself, both found by driving
full conversations through the actual code rather than reasoning about it on paper:**
- `domain.policy.record_question_asked` — the bounded-clarification counter increment —
  had existed and been unit-tested in isolation since phase 1, but nothing anywhere ever
  called it. The "give up after two attempts" mechanism could never have fired in an
  actual conversation before this module wired it in. It now does, end to end, verified
  by a test that runs an ambiguous field through two full clarification attempts and
  confirms the third turn accepts it with a recorded assumption and moves on.
- The first version of `advance()` only treated a clean "yes" as finalising when the
  prior phase was `REVIEW`/`COMPLETE`. Confirming the CONFIRM_INFERRED vehicle guess
  produced no patches either, so that branch never ran, `confirm_all()` was never called,
  and the same confirm-inferred question would have been asked forever. Fixed by
  including `CONFIRM_INFERRED` in the clean-confirm check.
- That fix then surfaced a second, subtler bug: with `CONFIRM_INFERRED` added, a clean
  confirm there also satisfied the "is_clean_confirm" condition used to decide COMPLETE
  vs REVIEW -- which would have skipped the whole-booking review after confirming only
  the vehicle guess. Confirming a vehicle suggestion is not the same act as confirming
  the finished booking; only a clean confirm made *while already reviewing* may finalise
  anything. Both are covered by dedicated tests now
  (`test_confirming_inferred_vehicle_and_helpers_together_reaches_review`, which
  intentionally does not just check "no longer stuck").

Not built here, and deliberately not: `extraction.suggested_reply` (the phase 2.4
escape hatch for off-topic/unclear turns) needs no new plumbing in this module. The
caller of `advance()` already holds the `ExtractionResult` it passed in, so checking
`suggested_reply` before deciding whether to call `templates.py` at all is a one-line
check that belongs in step 2.7's REPL, not something `machine.py` needs to forward.

**Step 2.6 was the first step this phase where the careful testing done on every prior
step actually paid off in advance:** no logic bugs surfaced, only one small polish item
(`format_vehicle`'s "a Tata Ace" reads fine in a sentence but wanted its article stripped
for a label-value table line — fixed as `_without_article`, without touching
`format_vehicle`'s existing contract, which `templates.py`'s prose still depends on as-is).

One design correction this step forced, caught before it shipped rather than after:
`docs/design.md` SS5.6's original example showed `Vehicle Tata Ace (suggested)`. Once
SS2.5's phase machine was built, that annotation became something that can no longer
happen: `REVIEW` is only reached once `CONFIRM_INFERRED` has resolved (or was never
entered at all, for a no-items booking), so a summary can only ever show `vehicle_type` as
absent or `CONFIRMED` -- never a still-unconfirmed suggestion. Doc corrected to match.

Noted, not fixed: `Address.raw_text` is a fifth field of the exact shape `booking_type` /
`schedule.is_asap` / `notes` were (step 2.2's findings) -- defined in the schema, confirmed
by `confirm_all`, but nothing anywhere ever populates it, because locality has no separate
raw-phrase-then-normalise pipeline the way dates and times do. Left out of the summary
renderer entirely (referencing it would be dead code for a status that cannot occur) rather
than fixed here, since removing or wiring up a Phase 1 schema field is a decision worth
raising on its own, not folding into an unrelated step.

**Retroactively fixed** during a pass that swept every step's writeup in this file for a
"noted, not fixed" item left dangling. `reducer._write_locality_raw_text` now stashes the
patch's `evidence` into the sibling `raw_text` field whenever `pickup.locality` or
`drop.locality` is set or corrected -- modelled on `_apply_time_patch`'s already-established
"one patch, two Field writes" shape, so `raw_text` gets the same CONFIRMED-guard and
revision history as every other field for free. `summary._render_address` now surfaces it
as `Koramangala (heard as "Kore Mangala")`, but only when the two actually diverge
(`_mentions_locality`: the interpreted value must be a substring of the raw evidence) --
otherwise the common case ("from Koramangala" quoted as evidence for a value of
"Koramangala") would flag every single booking for no reason.

Live-probed against the real extractor before trusting the design, the same discipline as
every other step: fed both a clean utterance and one with deliberately garbled locality
text ("kore mangala", "white feeld"). In both cases the model returned `value == evidence`
verbatim -- Rule 1 ("extract only what the user actually said, never infer a plausible
value") means the extractor does not attempt to "clean up" a mangled name into a confident-
looking real place, it just passes through whatever text it was given. So in practice
`raw_text` mostly echoes `locality` rather than routinely catching a divergence, which is a
narrower finding than design.md SS3.4's original framing implied. The fix is still correct
and worth having: it costs nothing when the two agree, and it is now actually wired up to
catch the case where they don't -- including real mangled STT output, which cannot be
tested until step 3.4 wires a live microphone into this same path. Regression tests in
`test_reducer.py` and `test_summary.py`.

**Step 2.7 found the most significant bug in this project so far, and it took the full
pipeline running live to surface it.** Scripted conversation, real API, real turns:

```
User: I need to move a sofa from Koramangala to Whitefield tomorrow evening.
Agent: Got it -- ... a sofa. What kind of things need to be moved?
User: third floor, and yes there is a lift.
Agent: Got it -- the 3rd floor, there's a lift. What type of items are these --
       furniture, appliances, or general household stuff?
User: ground floor.
Agent: Got it, updated -- the ground floor. What are we moving -- furniture, ...?
```

Three turns running, the agent asked the same question while the user answered three
*different* ones -- and one of those stray answers landed on the wrong field: "ground
floor" (meant for the never-reached drop-floor question) instead overwrote pickup.floor,
which the reducer correctly logged as a *correction* since nothing told it otherwise.

Root cause: `goods.category` was a directly-asked `Required` field, but the extractor's
own Rule 1 ("never infer a value the user did not state") correctly refuses to guess
"furniture" from "sofa" -- categorising an item *is* the kind of inference that rule
exists to forbid. Almost no one naturally says the word "furniture" out loud; they name
the things they're moving. A field that can only be filled by the user saying a category
word gets stuck forever, and everything gated on it (floor questions, packing) never
engages -- exactly the "avoid asking for information already provided" / naturalness
criteria the brief evaluates against, failing in the most visible way this project has
produced.

Fixed by extending the same pattern already proven for `vehicle_type`/`helpers_required`:
`domain.inference.infer_category` classifies the goods category deterministically from the
item list (a keyword lookup, explainable in one sentence, no model involved). This
sidesteps Rule 1 entirely -- it is not the model inferring an unstated fact, it is code
classifying a fact the user already gave explicitly (the item name). `goods.items` is now
unconditionally required (SS3.3 in design.md), removing a circular dependency the original
design had (items needed category to know if they were required; category needed items to
be inferred from).

One bug in the fix itself, caught by a test before it shipped: the first version set the
auto-inferred category to `PROVIDED`, indistinguishable from a value the user stated
themselves -- a "documents"-only booking that later added a sofa would have kept reporting
`parcel_documents` forever, since nothing re-checks a field that already looks confirmed.
Using `INFERRED` status instead (verified safe: `unconfirmed_inferred()` is keyed off
`FIELD_SPECS` membership, and `goods.category` has none, so it can never leak into the
confirm-inferred question flow the way giving it a spec entry would) keeps it re-inferrable
exactly like the vehicle guess, while `_needs_items`'s old category-gating logic being
removed meant nine existing tests needed updating to the corrected behaviour, not reverting
-- each one had encoded the old, circular design as its expected outcome.

After the fix, the identical scripted conversation ran to completion correctly: every
question asked was the one actually pending, every answer landed on the right field, the
inferred vehicle was confirmed, the whole-booking summary was confirmed, and the process
exited cleanly at `COMPLETE` -- the first full realisation of MASTER_PLAN.md's phase 2
acceptance criterion, live, not simulated.

---

## Phase 3 — Voice (Day 3)

**Goal:** the same conversation, spoken.

| # | Step | Files | Acceptance |
|---|---|---|---|
| 3.1 | STT service | `services/stt.py` | ✅ Groq `whisper-large-v3-turbo`. Empty/noise transcripts handled without an LLM call. |
| 3.2 | TTS service + audio cache | `services/tts.py` | ✅ Groq Orpheus. Sentence chunking (a self-imposed 200-char chunk size, not an enforced limit -- see findings below). Cache keyed by text hash. Browser `speechSynthesis` fallback flag. |
| 3.3 | Fast-path classifier | `conversation/fastpath.py` | ✅ Yes/no, meta-commands, bare numerics. **Fails open to the LLM.** |
| 3.4 | `/turn` endpoint | `api/routes.py` | ✅ Orchestrates STT → fastpath/extract → reduce → policy → template → TTS. |
| 3.5 | Voice UI | `frontend/src/` | ✅ Mic button, VAD silence detection (~700ms), audio playback, transcript. Calls the API by relative path (`/api/turn`, `/api/session`), never a hardcoded origin -- see Phase 4's CORS/HTTPS note above for why that single choice matters for both. |
| 3.6 | Live state panel | `frontend/src/` | ✅ Fields fill as they are captured (built alongside 3.5); corrections now render as `Kakkanad (was: Kochi)` -- see findings below. This is the evidence exhibit for the whole architecture. |

**Acceptance:** a complete booking spoken end to end on localhost, under ~2.5s per turn.

**Step 3.1 was probed live before the classifier was written, not after.** The acceptance
criterion needed a real answer to "what does empty/noise audio actually look like coming
back from Groq's Whisper?" rather than an assumption, so three rounds of synthetic audio
(true digital silence, low-amplitude noise, random/tonal noise, at durations from 0.3s to
3s) went to `whisper-large-v3-turbo` before any production code existed:

- Silence and low-amplitude noise deterministically transcribe as `"Thank you."` — a
  documented Whisper quirk (captioned-video training data) reproduced against this
  project's own model rather than taken on faith.
- Random/tonal noise comes back as a lone punctuation mark (`" ."`), which needs no
  hallucination list at all — stripping punctuation and checking for anything
  alphanumeric catches it for free.

`services/stt.is_noise` is exactly those two checks, kept deliberately narrow: nothing
keys off transcript length, so a genuine short answer like "yes" is never at risk. One
plausible-looking alternative was tried and rejected: Whisper's `verbose_json` response
exposes a per-segment `no_speech_prob`, which reads as the more "principled" signal — live
testing showed Groq's deployment returns `0` for every segment regardless of actual audio
content, so it carries no information on this API and would have been dead weight dressed
up as rigor. `tests/unit/test_stt_live.py` re-runs the original probe as a permanent,
sub-cent live test so a future change in Groq's behaviour is caught by the suite rather
than discovered mid-demo. See docs/architecture.md's "Detecting empty/noise transcripts"
for the full probe table.

`transcribe()`'s retry set deliberately differs from `llm/extractor.py`'s: a 400 there is
retried because it is the observed strict-mode schema-violation quirk a retry can plausibly
fix; a 400 on the transcription endpoint was reasoned to mean something structurally wrong
with the request instead. That reasoning first shipped as an analogy to extractor.py, not
as something actually tested — caught on review, since every other claim in this step had
been verified live and this one had not. Probed the same way as the noise behaviour above:
malformed bytes, a zero-byte file, an unsupported extension, and audio shorter than Whisper's
minimum all return a 400 with a specific, human-readable structural message (`"could not
process file - is it a valid media file?"`, `"file is empty"`, `"file must be one of the
following types: [...]"`, `"Audio file is too short..."`), and sending the exact same
malformed request twice in a row reproduces the identical message both times — direct proof
an identical retry cannot help. The reasoning held; it just wasn't proven yet when first
written down. `test_stt_live.py::test_malformed_audio_is_a_structural_400_not_a_transient_one`
keeps that proof live going forward.

**Step 3.2 is a case study in why this project insists on a live call over trusting
documentation, including its own -- two things written down here as verified fact turned out
to be wrong once actually tested.** Before writing any code, a web search for Orpheus's
per-request character limit returned conflicting claims -- one page said 200 characters,
another said "keep it under 10K" -- so the model-specific Groq doc page was fetched directly
to settle it: 200 characters, stated as a hard limit. Voices
`autumn/diana/hannah/austin/daniel/troy` were also confirmed from the docs, so
`settings.groq_tts_voice = "hannah"` (set back in phase 0) is a real, valid voice. `wav` was
confirmed as Orpheus's only supported `response_format` (the SDK's own type hints list
`flac/mp3/mulaw/ogg/wav`, inherited from the older PlayAI TTS models Orpheus replaced).

The first live call failed every time with a 400 that no doc mentioned at all:

```
The model `canopylabs/orpheus-v1-english` requires terms acceptance. Please have the org
admin accept the terms at https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english
```

Six requests (different lengths, both response formats, an invalid voice) all failed
identically -- the same "retrying cannot help" signature step 3.1 already established for a
structural 400, so `_synthesize_chunk` excludes `BadRequestError` from its retry set for the
same reason `services/stt.py`'s does. This needed one manual action outside this codebase --
an org admin accepting Orpheus's model terms at the URL above -- which has since been done.

**Once unblocked, the first real call immediately found a second bug, this time in the test
file, not the product code**: three of four live tests failed with `response_format must be
one of [wav]`, because `test_tts_live.py`'s `_speak()` helper omitted `response_format`
entirely, relying on a server-side default that does not exist for this model.
`services/tts.py`'s own `_synthesize_chunk` always passes it explicitly and was never
affected -- this was a test-only gap, fixed by defaulting it in `_speak()` too.

**Then a length sweep disproved the "200 characters is a hard limit" claim from the docs**,
which this file had itself repeated as settled fact one step ago. `test_text_one_character_
over_the_limit_is_rejected` expected 201 characters to be rejected; the real API accepted it
without complaint, and a follow-up sweep found no length-based rejection at all through 1000
characters. The wall that does exist is a `RateLimitError` reporting a **tokens-per-day (TPD)
limit of 3600** for this model on the free/on-demand tier -- separate from, and far tighter
than, the LLM's own free-tier limits already in the risk register, and already partly spent
by this step's own testing. The disproven test was removed rather than patched, since there
was no real boundary left to assert; `_MAX_CHARS = 200` is kept in `services/tts.py`, but its
justification changed from "Groq's enforced limit" to "our own chunk size, chosen for
streaming latency (architecture.md's mitigation 3) and to spend the newly-discovered TPD
budget more slowly." One related gap is flagged rather than fixed here: `RateLimitError` is
retried immediately with no backoff, which cannot help a "try again in 5h40m" TPD exhaustion
-- correct fix belongs to phase 4.4's cross-cutting error-handling pass across all three
service modules, not a one-off change to this one.

With both bugs fixed, live synthesis was confirmed working end to end: a real short phrase
("Got it, Koramangala to Whitefield.") produced ~142KB of playable WAV audio in one call.
Further live stress-testing (e.g. finding an actual upper bound) was deliberately not
pursued once the TPD constraint appeared -- it is a shared, scarce resource for the rest of
this project, not something to spend chasing a boundary that turns out not to gate anything.

Everything not gated on the two live findings above was correct on the first pass:
`chunk_text()` (16 unit tests, zero mocking) packs whole sentences greedily and falls back to
a word-boundary split for the one sentence longer than the chunk size on its own;
`synthesize()`'s on-disk cache (keyed by a hash of model+voice+text) is proven with real file
I/O via pytest's `tmp_path` -- a repeated phrase costs one mocked API call, not two, and a
different voice does not share the other voice's cache entry. Chunks are returned as a list
of separate WAV byte-strings rather than concatenated into one file: WAV's header encodes a
single length, so gluing complete WAV files together produces a malformed one, and keeping
them separate is what lets the caller stream-play the first chunk while later ones are still
being synthesised.

**Step 3.3 needed no live call at all -- `conversation/fastpath.py` is pure, deterministic
code, and every claim about it is checkable by running the suite.** Still verified the same
way as everything else: not by trusting that the code type-checks, but by driving it through
the real conversation machine. `test_fastpath_integration.py` runs a full furniture booking
(locality, item, date, time -> floor -> lift -> floor -> lift -> disassembly -> vehicle guess
-> review -> complete) using nothing but real `classify()` output fed into
`conversation.machine.advance`, asserting at every step that the fast path actually fired
rather than silently declining and passing for the wrong reason. It did, correctly, on the
first run -- confirmed by also tracing the full turn sequence outside the test to see every
intermediate `SlotDecision` and produced patch, not just trusting seven green dots.

Two things worth naming precisely, since they were easy to get subtly wrong:

- A `CONFIRM_INFERRED` decision's own field (`service.vehicle_type`, an `ENUM`) must never be
  consulted when a bare "yes"/"no" arrives for it -- the question being asked is "does this
  guess look right?", not "what is the vehicle?", so yes/no always means confirm/reject there,
  regardless of the underlying field's answer type. Getting this backwards would have tried
  (and failed) to interpret "yes" as an enum value.
- `SlotReason.CONFLICT` produces `op: correct`, never `op: set` -- the field is already
  `CONFIRMED` (that is why it is a conflict at all), and `set` against a `CONFIRMED` field
  is rejected by the reducer's own guard rather than resolving anything. Using `set` here
  would have silently produced a second, nonsensical conflict instead of resolving the first.

One deliberate refinement from the original design sketch: docs/architecture.md described the
yes/no lexicon as "<=3 tokens, lexicon match", which reads as two separate mechanisms (count
tokens, then check a lexicon). Implemented as one instead -- exact string membership in a
curated phrase set after normalising case/punctuation/whitespace -- since that alone already
guarantees the same safety property (every phrase in the set happens to be <=3 tokens by
construction) without a redundant separate counting step. docs/architecture.md corrected to
describe what was actually built.

**Retroactively fixed during step 3.4: the numeric fast-path was close to dead code for real
speech.** Step 3.3's own tests were entirely mocked/text-driven and never caught this --
finding it needed the thing step 3.4 uniquely made possible: a fully live, unmocked round
trip where Orpheus synthesises real speech and Whisper transcribes it back, with the real
extractor and fast path running on whatever came back. A spoken "3" came back from Whisper as
the word `"three..."`, not a digit, and a spoken floor answer naturally transcribes as
`"third"` or `"third floor"` -- essentially never as a numeral. The original digit-only regex
(`\d+`) is correct for typed input but had almost no real chance of ever firing for actual
speech, silently defeating a chunk of this step's own point (saving LLM calls on the most
common follow-up answers: floor number, helper count). Fixed in `fastpath._parse_integer`:
accepts a bare digit, a bare cardinal word ("three"), or a bare ordinal word ("third"),
optionally followed by one trailing unit noun ("floor(s)", "helper(s)") stripped only when it
is the *last* word -- re-verified against the exact adversarial case this could have broken
(`test_the_trailing_unit_strip_is_anchored_not_a_general_word_removal`: "the floor is third,
but there's no lift" still correctly declines, since the strip is anchored and "floor" is not
the last word there). `NUMBER_WORDS` promoted from private to public in `domain/normalizers.py`
(mirroring `WINDOW_START_HOUR`'s earlier promotion) so fastpath.py reuses the one word list
instead of hand-typing a second copy. Re-verified live end to end after the fix, with an
instrumented call counter proving zero LLM calls: a real synthesized "three" now correctly
sets `pickup.floor = 3` and advances to the next question, where the same phrase originally
caused the extractor to misattribute "three" as an item-quantity correction instead (a real,
observed extraction misfire this fix also sidesteps entirely by not needing the LLM for this
answer at all).

**Step 3.4 is the first step that actually runs every prior piece together in one process,
and building it this way -- rather than one giant endpoint function -- surfaced its own
findings before any live call was even made:**

- `conversation/orchestrator.py` is new: the "understand -> advance -> decide what to say"
  sequence that lived inline in `tests/repl.py` since step 2.7 is now shared between the REPL
  and `/turn`, extracted rather than duplicated -- the exact drift risk `booking_type` and
  `schedule.is_asap` already demonstrated once when the same logic existed in only one place
  and silently went unused elsewhere. `tests/repl.py` still calls the LLM unconditionally on
  every turn, by design (it exists to exercise extraction quality); `/turn` calls the same
  shared tail (`finish_turn`) after either a fast-path hit or that same LLM call, whichever
  applies. Verified the refactor changed nothing observable by re-running a live REPL turn
  against the real API before building anything else on top of it.
- `settings.max_clarify_attempts` was defined in `config.py` since phase 0 and read by nothing
  -- `machine.advance()` never accepted it, so `domain.policy`'s bounded-clarification
  threshold could never actually be configured, the same orphaned-config shape as
  `booking_type`/`schedule.is_asap` before step 2.2's fixes. Found by asking "what do I
  actually pass to `advance()` here?" while wiring the endpoint -- fixed by threading an
  optional `max_clarify_attempts` parameter through `advance()`, `finish_turn()` and
  `process_utterance()`, defaulting to `policy.DEFAULT_MAX_CLARIFY_ATTEMPTS` so every existing
  caller is unaffected.
- `app/session/store.py` is a plain in-memory `dict`, deliberately -- this is a single-instance
  demo deployment (README's assumptions and limitations), not a service that needs Redis or a
  database. `Session` carries the previous turn's `SlotDecision` alongside the conversation, so
  `fastpath.classify()` on the *next* utterance always has the real pending-question context
  rather than something reconstructed from rendered text.
- `get_settings()` was being called directly inside route handlers rather than injected via
  FastAPI's `Depends`, unlike the Groq client -- meaning a test could override the client but
  not the TTS cache directory. Refactored to `Depends(get_settings)` for the same reason the
  client already used DI: so `test_api_turn.py` can point the cache at an isolated `tmp_path`.
  This was not a hypothetical concern -- it caused a real, confirmed bug in this step's own
  test suite (next finding).
- **A shared, real, on-disk TTS cache silently invalidated a test.** The first version of
  `test_process_turn.py` used the real configured `settings.tts_cache_dir` directly. A test
  asserting "TTS failure still returns agent_text with no audio" mocked `speech.create` to
  raise a connection error -- and passed anyway, with real cached bytes from a *different*
  test that happened to produce the same response text, because `tts.synthesize`'s cache
  lookup runs before any API call and found a hit on disk left over from another test in the
  same run. Every test now gets its own `tmp_path`-backed cache dir via a fixture. Caught by
  the test itself failing once the assertion was tightened enough to notice the wrong bytes
  coming back -- not found by inspection.
- **`test_api_turn.py`'s `TestClient(app)` mutates the same module-level `app` object every
  other test file importing `app.main` also shares** (`test_health.py` included).
  `app.dependency_overrides` is cleared in a fixture's `finally` block for exactly this reason
  -- confirmed by running the full suite together, not just this file alone, since an override
  leaking across files would not necessarily fail the file where it was set.

Two layers of testing, deliberately: `test_process_turn.py` calls the pipeline's private
`_process_turn` directly with a mocked three-API client (STT/LLM/TTS call counters, so a test
can assert not just the right output but that the right APIs were even touched -- e.g. a noise
transcript truly never reaches the LLM). `test_api_turn.py` separately proves the FastAPI
wiring itself -- multipart parsing, dependency overriding, response serialisation -- using a
real `TestClient` request. Both were necessary: the first catches logic bugs precisely, the
second catches the class of bug that only exists at the HTTP boundary.

Then verified against the real, fully unmocked stack -- not just mocks -- exactly the pattern
that found the numeric fast-path gap documented above: Orpheus synthesises real user speech,
`_process_turn` transcribes it with real Whisper, runs the real fast-path/extractor/reducer
chain, and synthesises the real response back with Orpheus again. A live instrumented call
counter confirmed a fast-path-eligible turn made zero LLM calls, not just that the response
looked right.

319 tests passing offline (10 more are `llm`-marked, requiring a live key). `docs/architecture.md` corrected in two places (the fast-path table and the
numeric-matcher description); `MASTER_PLAN.md`'s own step 3.3 entry above corrected rather
than silently edited, per this file's own convention of recording what was believed and what
was actually found.

**Step 3.5 started from a real design, not a blank page.** A landing-page mockup (Claude
Design's own `.dc.html` export format -- a self-decompressing bundle of gzipped/base64 assets,
not plain HTML) was decoded with a small script that replicates its own bootstrap logic
(base64-decode, gunzip, extract the `__bundler/manifest` and `__bundler/template` script
tags) rather than trying to execute the bundle's JS directly. That surfaced the actual page
structure, copy, colours (`#F1ECE3` cream / `#111110` ink / `#FF3D1F` orange / `#D9F25E`
lime), and fonts (Bricolage Grotesque, Geist Mono) cleanly, plus a scripted fake demo
conversation showing the intended interaction shape. Rebuilt as real React/TS
(`frontend/src/App.tsx`, `styles.css`, `api.ts`, `audio.ts`, `useRecorder.ts`) rather than
embedding the mockup's own template-DSL runtime, since that format has no relationship to
this project's actual Vite/React stack -- the fake scripted conversation was replaced with
real `POST /api/session` / `POST /api/turn` calls end to end.

Mic capture uses `MediaRecorder` gated by an independent Web Audio API voice-activity check
(`useRecorder.ts`): silence is only measured *after* real speech is first detected, so a
thoughtful pause before speaking is never mistaken for "finished talking" -- an easy mistake
a naive amplitude-threshold VAD makes, caught by reasoning through the sequence before
writing it, not after. A max-recording safety timeout guards against VAD never firing at all
(background noise). The RMS silence threshold is a constant flagged for by-ear tuning against
a real microphone, per docs/test-plan.md's manual checklist -- this environment cannot grant
microphone access to verify it (see below).

**The most significant bug here needed a real browser to find, the same pattern as step
3.4's numeric fast-path gap.** The first version guarded `POST /api/session` against React
19 StrictMode's dev-only mount-cleanup-remount cycle with a ref flag (to stop it from firing
twice) paired with the standard `cancelled`-on-cleanup pattern (to stop a stale response from
updating state). Those two idioms actively fight each other here: StrictMode's simulated
cleanup still runs once between the two invocations regardless of the ref guard, so the
`cancelled` flag it sets discards the response from the *one real request* the guard
correctly allowed through. The network tab showed a correct 200 with the right greeting text;
the UI never showed it. Fixed by dropping the `cancelled` pattern entirely -- it was solving
a problem (a genuine unmount) this root, un-routed component never actually has, and fighting
a problem (StrictMode's simulated remount) it was never designed to handle. Caught by loading
the real page in a browser and watching the greeting simply never appear, not by reading the
code -- the same "drive it for real" discipline as every other step, applied to the frontend
for the first time.

**Verified as thoroughly as this environment allows, and no further:** the in-app preview
pane cannot grant microphone access (confirmed directly -- it reports the request and blocks
it), and Claude in Chrome was unavailable this session, so real mic capture and VAD timing
are unverified pending the manual checklist on a real device. Everything else was verified
live: the static design against the decoded mockup pixel-for-pixel; session bootstrap with
real Orpheus-synthesised greeting audio; a full `/api/turn` round trip through the actual
browser `fetch`/`FormData` code path (a synthetic silent WAV, correctly classified as noise
server-side and correctly re-prompting client-side, with zero LLM calls); the mic-permission-
denied error path rendering correctly instead of crashing; and both `npm run typecheck` and
`npm run build` clean.

**Found and fixed one more thing along the way, in already-committed local state rather than
code:** the real `backend/.tts_cache/` directory had six stale 9-byte files
(`base64("wav-bytes")`) left over from iterating on step 3.4's tests before their cache-dir
isolation fix landed -- caught when the live greeting audio came back suspiciously small and
decoded to literal mock bytes. The committed test suite was already correct (verified by the
passing tests); this was leftover local dev-environment state, not a code defect, cleaned up
by deleting the stale entries. `.tts_cache/` is gitignored, so nothing here was ever at risk
of being committed.

**Not done in this step, flagged for 3.6 explicitly rather than silently rolled in:** the
live state panel's "corrections render as `Kakkanad (was: Kochi)`" requirement needs
`Field[T].revisions` (already in every backend response's `state`) surfaced in the UI --
`api.ts`'s current `FieldValue<T>` type deliberately only models `value`/`status`, the
minimum 3.5 needed. `.claude/launch.json` created at the workspace root (one level above this
repo, where the preview tool actually looks) so `backend`/`frontend` dev servers can be
started for preview; the backend entry runs through `cmd /c cd ... &&` rather than
`--app-dir`, since `--app-dir` only changes uvicorn's import path, not the process's working
directory that `config.py`'s `.env` resolution depends on -- found by the exact same "real
key configured locally, backend reports it as missing" symptom this project has now seen
enough times to recognise immediately.

**Step 3.6 turned out to need no backend change at all -- the data it needed was already
being sent, just not modelled or read.** `api/routes.py`'s `/turn` and `/session` handlers
already return `booking.model_dump(mode="json")` in full, and `Field[T].revisions` (a list
of `{value, status, evidence, turn}`, pushed onto by `reducer.py`'s `_write_scalar` on
every `op: correct`) was already inside that JSON, unused. Confirmed live rather than
assumed, exactly this project's standing discipline: a script drove two real turns through
the actual `_process_turn` pipeline with real synthesised speech ("I'm moving from Kochi to
Whitefield." then "Actually, make that Kakkanad, not Kochi.") and printed the resulting
`pickup.locality` JSON --
`revisions: [{"value": "Kochi", "status": "ambiguous", "evidence": "from Kochi", "turn": 1}]`
came back exactly as `domain/state.py`'s `Revision` model promises. `api.ts` gained a
`Revision<T>` type and `FieldValue<T>.revisions`, both typed directly off that confirmed
shape rather than guessed; `App.tsx` gained one small pure function, `displayValue()`,
which formats a field's current value and appends `" (was: <previous>)"` when
`revisions` is non-empty -- reading the *last* element specifically, since `revisions` is
append-only and the last entry is the value immediately before the current one, not the
first-ever value (`revisions[0]` would be that, and would be wrong after a second
correction). Both the current and previous value are run through the *same* formatter
(`prettify` for the vehicle type, the date formatter for the schedule date), so a
corrected vehicle type reads "Tata Ace (was: Mini Truck)" rather than a raw enum sitting
next to a prettified one. The combined "When" row (date + time window are two separate
backend fields shown as one line) formats each half's correction independently, so a
date-only correction and a time-only correction each surface in the right half of the
line rather than being ambiguous about which one changed.

Verified: `npm run typecheck` and `npm run build` both clean; the live probe above
confirms the exact JSON contract this code now depends on; a browser regression check
confirmed the app still loads and behaves identically on the common, no-correction-yet
path (nothing regressed for the far more frequent case where a field was only ever stated
once). Not verified, and cannot be here: the actual on-screen "(was: Kochi)" text in a
real spoken correction, which needs a real microphone this environment does not have --
the same limitation step 3.5 already hit and documented, not a new one.

**Retroactively fixed after real-browser testing surfaced two problems step 3.5's own
"verified as thoroughly as this environment allows" note had explicitly flagged as
unverifiable here: Kerala/Karnataka place names, including local ones, were not being
recognised reliably, and there was "so much lag" that speech often was not captured at
all.** Both needed the same discipline as everything else in this project -- probe live,
don't assume -- and the first fix's own side effect needed a second round of the same
discipline to close properly.

*Place names.* Whisper's `prompt` parameter (OpenAI's own documented, fine-tuning-free
technique for biasing proper-noun recognition) was added to `stt.transcribe()` as
`_LOCALITY_PROMPT` -- a representative spread of real Bengaluru localities and Kerala
places, not an exhaustive gazetteer (Whisper's own prompt budget is ~224 tokens; the
prompt conditions the *kind* of word expected next, it does not need to enumerate every
place this project might ever hear). Verified with a controlled live A/B, not assumed
from the technique's reputation: the same four phrases synthesised and transcribed with
and without the prompt improved on 3 of 4, regressed on none.

That same prompt, live-reprobed against silence and noise, widened what Whisper
hallucinates well past the "Thank you." step 3.1 had verified and built `is_noise` against
-- samples now included "Pag.", a stray URL, several seconds of one word looping, and,
overwhelmingly the most common shape, the prompt's *own text* echoed back verbatim
("Kerala places.", "Kerala places, Kosovo."). `is_noise` gained two more checks: a
structural repeated-word-loop detector (4+ identical words running -- generalises across
any future hallucination vocabulary shift, unlike a phrase list) and a prefix check for a
verbatim echo of `_LOCALITY_PROMPT`'s own section-header text. Two confidence-signal
alternatives were tried and rejected before settling on text heuristics: Whisper's
`no_speech_prob` was already known (step 3.1) to always read `0` on this API; `avg_logprob`
from `verbose_json` was tested fresh here and showed overlapping ranges between silence
(-1.70 to -0.44) and real speech (-0.37 to -0.18) -- no threshold separates them without
also rejecting genuine answers.

This left a residual, initially *accepted* gap -- live samples like "If you feel like a"
matched none of the checks -- reasoned to be safe because the extractor's own existing
Rule 1 machinery was live-tested and confirmed to catch what leaks through: fed garbage
transcripts against a realistic pending question, ambiguous-sounding leaks came back as
low-confidence patches with an `ambiguity` reason attached, the same path a genuinely vague
real answer takes, not a silent fact. **That backstop does not hold unconditionally, and
trusting it without testing the exact claim being relied on would have shipped a real bug:**
a live rerun of `test_stt_live.py` (rewritten to assert this layered property end-to-end,
not `is_noise` in isolation, since the latter was now known not to always hold) caught
silence transcribed as "Kerala places, Kosovo." coming back from the *real* extractor as
two separate `PROVIDED` patches (`pickup.locality = "Kerala places"`,
`drop.locality = "Kosovo"`) at confidence 1.0 with **no ambiguity flag at all** -- exactly
the silent-corruption outcome the whole design exists to prevent, and the backstop missed
it precisely because a verbatim place-name-shaped hallucination reads as confident, genuine
input to an LLM extractor, not as something to question. Root cause: `_PROMPT_ECHO_PREFIXES`
did not exist yet. Added, re-verified live immediately after (three passing runs; the same
prefix showed up a fourth and fifth time, live, confirming it is the dominant hallucination
shape for this prompt, not a one-off), and the test suite now encodes the real contract
this design depends on -- catch the common, dangerous shape cheaply before any LLM call, and
for whatever still leaks through, assert the extractor never hands back an unflagged patch,
rather than asserting the first layer alone catches everything, which live testing had just
disproven.

*Lag.* Two independent contributors, found by reasoning through what "doesn't take in what
I say" actually implies rather than guessing at one fix: the frontend's voice-activity
detection, and backend TTS latency.

`useRecorder.ts`'s VAD used a single fixed RMS constant -- exactly the thing step 3.5's own
write-up had already flagged as unverifiable without a real microphone, and a real
microphone is exactly what surfaced it as wrong. Root cause: `getUserMedia({audio:true})`
enables the browser's default automatic gain control, which continuously renormalises input
levels, so no fixed number stays calibrated across devices, rooms, or even across a single
session as ambient noise shifts -- speech would fail to register, or silence would fail to
end a recording, unpredictably. Replaced with an adaptive noise floor: the ambient level is
tracked continuously, but only updated while the frame is already classified quiet (so a
loud word never drags the baseline up and makes the *next* word look like more silence),
and speech is judged as a fixed margin above that running floor rather than against a
constant. A throttled `console.debug` line (rms/floor/threshold/speaking state, every 2s)
was added deliberately, since this environment still cannot grant microphone access to
verify the algorithm directly -- it exists so a future report can be diagnosed from browser
console output alone rather than guessed at blind a second time.

`services/tts.py`'s `synthesize()` was awaiting each response chunk's synthesis one at a
time in a `for` loop. Harmless for a short one-chunk reply, but the final booking-summary
readback routinely spans multiple chunks, and a sequential loop pays the full per-chunk
network latency once per chunk instead of once total -- a real, measurable tax against this
project's own latency budget (docs/architecture.md). Rewritten to fan the same calls out
through `asyncio.gather`, which preserves chunk order (result order matches input order,
not completion order) and the existing cache-hit-skips-the-network-call behaviour without
any change to callers. A timing-based test was added (`test_tts.py`) specifically because
the existing count/content-only tests could not have caught a regression back to sequential
awaiting -- only elapsed time can.

**Verification:** offline suite green (`ruff check`, `ruff format --check`, full `pytest`
run, backend); the live `test_stt_live.py` suite re-run against the real API both before and
after the `_PROMPT_ECHO_PREFIXES` fix, confirming the failure and then confirming the fix,
not just trusting the reasoning; `npm run typecheck` and `npm run build` clean on the
frontend. Browser-pane verification went as far as this environment allows (same
constraint as step 3.5): session bootstrap, greeting playback, and the mic-permission-denied
error path all still render correctly post-edit with no console errors; the adaptive VAD
algorithm itself and real-world place-name recognition still need the user's own microphone
and voice to confirm, which is exactly why the diagnostic logging above was added rather
than skipped.

**Retroactively fixed after the user's own real-browser testing found the mic permanently
stopped responding after the first turn -- the single most severe bug this project has
shipped, and one no amount of backend testing could have caught, since it lived entirely in
frontend state the backend never touches.** Root cause: `useRecorder.ts`'s `status` moved
`idle -> recording -> processing` on every turn, but nothing anywhere ever moved it back.
`handleMicClick` only handles `idle`/`error`/`recording`, so once `status` reached
`processing` after the very first recording, it stayed there forever -- every later tap on
the mic was silently a no-op. This had been invisible to every check this project had run
so far specifically because none of them exercised a *second* real recording through the
actual hook: the HTTP-level end-to-end test earlier in this same session drove `/api/turn`
directly with Python, never touching `useRecorder.ts` at all, and the extensive automated
suite has no frontend test runner to have caught it either. Fixed by having `onComplete`
optionally return a Promise and awaiting it in the `"stop"` handler, resetting `status` to
`"idle"` in a `finally` once the turn settles, success or failure alike, so a failed turn
still leaves the mic usable for a retry rather than stuck.

**Verified live, not just reasoned through, since this exact bug was invisible to reasoning
the first time:** this sandbox still cannot grant real microphone access, so real audio
input was synthesised entirely in-browser instead -- a `MediaStreamAudioDestinationNode`
fed by an oscillator, installed in place of `navigator.mediaDevices.getUserMedia`, produces
a genuine `MediaStream` indistinguishable from a real mic at the API level. Clicking the
real mic button twice in a row, with the oscillator torn down mid-recording so the VAD's
real silence-detection logic (not a mock) drove the auto-stop, produced two consecutive
real `POST /api/turn` round trips before the fix would have allowed only one.

**That same synthesised-audio test caught a second, independent, genuine bug along the
way -- a raw 500 where the whole exchange should have degraded gracefully.** The encoded
clip (real audio-track data, but carrying no actual signal for its full 20-second
max-recording safety-net duration) made Whisper return a structural
`invalid_media_file` 400. `services/stt.py` deliberately lets that propagate rather than
retrying it -- correct, an identical retry on a malformed file cannot help, and this exact
reasoning already has its own passing test (`test_transcribe_does_not_retry_a_bad_request_
error`). But nothing above `transcribe()` ever caught what it propagates, so it reached
FastAPI's default handler as an unhandled 500 instead of the turn's own, already-tested "no
usable speech" reprompt path -- a real, plausible field failure (a muted or disconnected
microphone recording nothing for the full safety-net window), not just a synthetic-test
artifact. Fixed at the one place it actually belongs: `api/routes.py`'s `_process_turn`
catches `groq.BadRequestError` around the `stt.transcribe()` call and treats it exactly like
the `None` result that branch already exists to handle -- not a new fallback, the existing
one's contract finally covering the one case that used to slip past it. Re-verified live
immediately after: the identical scenario that previously 500'd now returns 200 with a
graceful reprompt.

**Two UX gaps closed alongside, from the same real-testing pass:** no way to stop the agent
mid-response, and a spinning mic icon during "processing" that read as gimmicky rather than
as a load indicator. `audio.ts`'s `speak()` now returns a `{finished, stop}` handle instead
of a bare Promise -- `stop()` pauses whatever chunk is currently playing and resolves its
pending promise immediately rather than leaving the caller's loop waiting on an "ended"
event that pausing alone never fires. `App.tsx` shows an explicit "Stop" control while a
response is playing, and tapping the mic to start a new recording also interrupts any
response still playing first -- talking over the agent is the natural way to barge in, and
`isSpeaking` is deliberately independent of the recorder's own `status`, so the mic reads
"Tap to talk" (truly idle, not mid-turn) the moment a response starts playing rather than
staying artificially blocked until playback finishes. The processing spinner
(`conic-gradient` background rotating via `animation: spin`) is replaced with a plain
opacity/scale pulse -- a buffering indicator, not something that reads as the icon itself
spinning.

**A separate, non-bug finding from the same conversation, worth recording precisely because
it is not a code defect:** re-reading the assessment brief directly (not from memory)
confirmed it states outright that "UI design is not the primary focus of this assignment...
You do not need to spend significant time creating a highly polished or production-grade
UI," and its entire evaluation-criteria list is about the conversation/AI engineering, not
visual design. The current landing page (a full marketing page -- hero, testimonial
marquee, feature cards, a steps section, a CTA, a tech-stack row -- with the actual
interactive widget below all of it) is real, deliberate design work, but it is not what the
brief asks to be evaluated on, and it adds scroll distance between "open the URL" and
"interact with the voice agent" that works against the brief's own "make it easy to
interact with the voice agent" UI guidance. Flagged for a decision, not silently changed:
restructuring so the interactive widget is immediately visible needs the user's own call on
whether to trim or reorder the marketing sections, not something to unilaterally rewrite
mid-bug-fix.

**A third real bug from the same real-testing session, this time reported directly with a
full server traceback rather than found by further probing -- a date phrase crashed the
whole turn with a 500 instead of degrading gracefully.** The user said something that made
the real extractor propose `schedule.date = "September 7, Monday"`. `domain.normalizers.
resolve_relative_date` only recognised a fixed set of shapes (today/tomorrow/a bare or
modified weekday/"the Nth") -- this phrase matched none of them, so `domain.reducer`
correctly marked the field `AMBIGUOUS` per its own documented contract, but *also* stored
the raw, un-parsed phrase as the field's `.value` (the same "never discard what was heard"
choice locality's `raw_text` already makes). Nothing downstream expected that: `templates.
_schedule_fragment` tries to voice back *any* field that changed this turn regardless of
its status, calling `format_date_short(value)` -> `date.fromisoformat(value)` on a string
that was never ISO in the first place, crashing on the very first turn a date phrase falls
outside that fixed set -- not a rare edge case for a required field real users describe in
many different ways.

Fixed in two complementary layers rather than one, since they close two different gaps:

- **Root cause -- resolve more of what a real user actually says.** `dateutil`
  (`requirements.txt`'s own pinned dependency, referenced by
  `test_import_boundary.py`'s allowed-imports comment but never actually wired into any
  module until now) backs a new last-resort branch in `resolve_relative_date` for
  month-name-and-day phrases ("September 7", "the 7th of September", "Monday, September
  7"). `fuzzy=True` is needed for phrases with connector words a strict parse rejects --
  verified live before trusting it, not assumed: the same clearly-non-date phrases this
  project's own fastpath tests already use ("a sofa and two cupboards", "third floor", ...)
  were re-tested and still correctly return "no date here", not a misread one. Scoped
  narrowly on purpose -- this only ever runs on a phrase the extractor has already tagged
  as a `schedule.date` value, never over arbitrary text, so `fuzzy`'s permissiveness is not
  a blank cheque. A resolved date already in the past for the reference year rolls forward
  to next year, the same always-resolve-forward rule the weekday and ordinal-day branches
  already apply, for the same reason.
- **Last line of defence -- never let an un-resolvable phrase crash the turn.**
  `format_date_short`/`format_date_full` now fall back to the raw phrase verbatim on a
  `ValueError` instead of propagating one, since a genuinely unparseable phrase is still a
  real, designed-for possibility even with the improved coverage above: `domain.policy.
  _give_up_on_exhausted` accepts a date it could never resolve after the clarification
  budget runs out, flipping status from `AMBIGUOUS` to `PROVIDED` while *keeping* the same
  raw phrase as `.value` permanently -- meaning `conversation/summary.py`'s own final-summary
  renderer (which checks `status != EMPTY`, not `!= AMBIGUOUS`) was equally exposed to the
  exact same crash, later, for any date that reaches that give-up path. Separately,
  `_schedule_fragment` now also skips voicing the date at all while it is *still*
  `AMBIGUOUS` (as opposed to given-up-on-and-accepted) -- purely a conversational-quality
  fix once the crash itself cannot happen, since saying a phrase back as if understood the
  same turn the agent is about to ask about it again reads as confused, not helpful.

**Verified live, with the real extractor, not just the offline suite:** a fresh
conversation given "Let's do September 7th, a Monday, in the evening." resolved
`schedule.date` to `2026-09-07` (correctly a Monday) with status `PROVIDED` and a clean
"Got it — Monday, evening." response -- no crash, where the identical class of phrase
previously 500'd. New regression tests cover the exact reported phrase, several
realistic siblings, the past-date-rolls-forward rule, the non-date phrases must not be
misread by `fuzzy=True`, and the formatter/acknowledgment path surviving a phrase that
still cannot be resolved at all.

---

## Phase 4 — Deployment and resilience (Day 4)

**Deploy first thing in the morning, before any polish.**

| # | Step | Acceptance | Status |
|---|---|---|---|
| 4.1 | Single-service deploy (FastAPI serves the built frontend bundle) | Public HTTPS URL loads the app; `GROQ_API_KEY` and every other `.env.example` variable set on the **hosting platform's own dashboard**, not only in a local `.env` -- confirmed by `/api/health` reporting `llm_configured: true` on the deployed URL itself, not just locally | ⏳ Image built and locally verified (below); actually deploying to a chosen host is the remaining step |
| 4.2 | Microphone works on the deployed origin | Verified in a fresh browser profile, over the deployed HTTPS URL (`getUserMedia` is blocked on plain HTTP everywhere except `localhost`, so this can only be genuinely verified post-deploy, not in local dev) | ⏳ Blocked on 4.1 by construction |
| 4.3 | Cold-start mitigation | Either a warm instance or an explicit "waking up" UI state | ✅ UI state (`isSlowStart`) built; not yet observed against a real cold host |
| 4.4 | Error handling pass | Every external call has a timeout, a retry and a spoken degraded fallback | ✅ Rate-limit backoff added (`app/retry.py`); timeout/retry/fallback already existed since phase 3 |
| 4.5 | Session TTL sweep | Memory does not grow unbounded | ✅ |

**Acceptance:** a link that a stranger can open and complete a booking through, and that
degrades gracefully when STT, the LLM or TTS fails.

**4.4 and 4.5 needed no live call to build -- both are pure, deterministic logic checkable
by the suite -- but 4.4 specifically closes a gap this file flagged as future work in three
separate places already** (services/tts.py's and services/stt.py's own comments, and the
risk register's "a rate-limit retry can't help if the limit is a daily quota" row), so it
is worth naming precisely now that it is actually done rather than deferred again.

All three Groq-calling modules (`services/stt.py`, `services/tts.py`, `llm/extractor.py`)
originally retried a `RateLimitError` the same way as any other transient failure:
immediately, with no delay. Step 3.2's own live testing had already shown this cannot
work for every cause of a 429 -- a real Orpheus tokens-per-day exhaustion reported "try
again in 5h40m0s", which an instant, no-backoff retry cannot address at all, and *will*
hit again identically. `app/retry.py` is a small, shared, pure function
(`retry_after_seconds`) that reads the same `Retry-After` / `Retry-After-Ms` response
headers Groq's own SDK already parses internally for its own built-in retry logic (this
project's three modules replace that generic retry with their own domain-aware
one-retry-then-fallback shape, so the SDK's handling of the header never runs) -- and
caps how long it is willing to trust that header at 1 second, a deliberate choice given
these calls sit inside a single voice turn against docs/architecture.md's ~2-3s *total*
latency budget, not a batch job. A wait longer than that (or no header at all, e.g. a
plain per-minute throttle with no machine-readable hint) skips the retry entirely and
falls straight through to the existing, already-tested degraded fallback (`None` ->
re-prompt / `speechSynthesis` fallback / `FALLBACK_RESULT`) rather than blocking a turn
for a duration that would blow its latency budget on its own.

`llm/extractor.py` needed a slightly larger change than the other two to wire this in:
`_call()` previously swallowed every retriable error, including `RateLimitError`,
internally and returned a uniform `None` -- hiding the actual exception (and its headers)
from `extract()`'s own retry loop. Changed to let `RateLimitError` specifically propagate
out of `_call()` so `extract()` can inspect it, while every other retriable error is still
caught and flattened to `None` exactly as before. The repair pass's own single `_call()`
site is deliberately *not* given its own retry-after wait budget on top of this -- it is
already a second chance beyond the normal attempt budget, and a rate limit there is
treated the same as any other repair-pass failure always was: the safe fallback, not a
crash.

**4.5's session store already carried `created_at`/`last_active` since step 3.4**,
added specifically so this step would not need to restructure the module -- confirmed
true. `store.create()` and `store.get()` both gained an optional `ttl_seconds` parameter
(defaulting to `None`, i.e. every existing caller and test keeps the pre-4.5
lives-forever behaviour unchanged) rather than reading `app.config` directly, for the
same dependency-injection reason `api/routes.py`'s own `Settings` dependency exists --
step 3.4 found a route reaching for `get_settings()` itself, instead of receiving it, was
exactly what let a stale on-disk TTS cache silently invalidate a test, and TTL is the
same shape of testability risk. `get()` evicts an individual expired session on access
(so a session nobody ever recreates does not have to wait for someone else's `create()`
to notice it), while `create()` additionally sweeps every other idle-expired session
first, since new-session creation is the one call site guaranteed to run once per
distinct visitor -- a single visitor's own later turns only ever update their *own*
existing entry via `save()`, never add a new dict key -- which is exactly the point at
which unbounded growth would actually accumulate. No background task or its own
lifecycle needed.

**4.1, 4.3: a Dockerfile was written and its logic verified locally, since this
environment has no Docker daemon to build and run the image directly -- honestly
incomplete rather than claimed as done.** The multi-stage build (Node stage compiles
`frontend/dist`, copied into a Python 3.12-slim runtime stage alongside `backend/`)
reproduces the exact repo-relative directory shape `app/main.py`'s own
`FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"` depends on
(two levels above `app/main.py`, i.e. one above `backend/`) -- verified by reasoning
through the path arithmetic *and* by simulating it: a fresh, isolated virtualenv
installing only `requirements.txt` (no compiler needed -- confirmed, not assumed, since
`pydantic-core` and `uvicorn[standard]`'s `uvloop`/`httptools` all have prebuilt wheels
for this platform), then running the Dockerfile's exact `CMD` (`uvicorn app.main:app
--host 0.0.0.0 --port ${PORT}`) from a `backend/` working directory with the real built
`frontend/dist` one level up, correctly served both `/api/health`
(`llm_configured: true`, picked up from the real local `.env`) and the app's real
`index.html` at `/`. `.dockerignore` excludes `.env` explicitly -- a real key must never
be baked into an image layer that could end up pushed to a registry. What remains
unverified here: the container build itself (the `node:20-slim` stage's `npm ci`, the
multi-stage `COPY --from=`) and cold-start behaviour against a real spun-down instance,
neither of which this sandboxed environment can exercise. README.md's Deployment section
now gives concrete, host-agnostic steps (point any Docker-deploying host at the repo,
set every `.env.example` variable on *that host's own dashboard*, check `/api/health` on
the live URL) rather than the placeholder it held before -- picking a specific host and
clicking through its own account/dashboard is a decision and an action that belongs to
the user, not something to guess at or act on unprompted.

**Deliberately not a checklist item, because it is already structurally impossible to get
wrong: CORS.** README.md already states this plainly -- the Vite dev server proxies `/api` to
the backend in development, and FastAPI serves the built frontend bundle from the same origin
in production (`app/main.py`'s own docstring), so the browser never makes a cross-origin
request in either environment. There is no CORS middleware anywhere in this codebase because
there is nothing for it to do. This only stays true as long as the single-service deploy
shape in 4.1 does: **if a future deploy ever splits the frontend and backend across two
different hosts** (a separate Vercel frontend and Render backend, say), that reintroduces
cross-origin requests and `fastapi.middleware.cors.CORSMiddleware` becomes necessary again --
worth remembering precisely because the current plan makes it easy to forget the problem
exists at all. The same single-origin shape is also why the frontend (3.5/3.6) must call the
API by **relative path** (`/api/turn`, `/api/session`), never a hardcoded
`http://localhost:8000` -- a relative URL is also what makes the mic-permission HTTPS
requirement (4.2) automatic rather than something to remember: it inherits the page's own
origin and protocol for free.

---

## Phase 5 — Quality and evaluation (Day 5)

| # | Step | Acceptance |
|---|---|---|
| 5.1 | Run the full scenario matrix aloud, personally | Every scenario in `docs/test-plan.md` exercised |
| 5.2 | Extraction eval set (~40 cases) | Scored precision/recall on field, value and op |
| 5.3 | Golden conversation tests with recorded LLM responses | Replayed from disk: deterministic, fast, zero cost |
| 5.4 | Fast-path safety test | Adversarial corpus; asserts the fast path *declines* to match |
| 5.5 | Benchmark `gpt-oss-20b` against `gpt-oss-120b` | Model choice backed by measurement, not assumption |
| 5.6 | Prompt tuning against observed failures | Only real failures, never imagined ones |

---

## Phase 6 — Documentation and demo (Day 6)

| # | Step | Acceptance |
|---|---|---|
| 6.1 | README: setup, env vars, run, deploy, live URL | A stranger can run it locally from the README alone |
| 6.2 | README: assumptions and limitations | Explicitly required by the brief. Must state plainly which browser was tested (test-plan.md's manual checklist requires Chrome) and whether others were even tried -- an evaluator has no reason to think to switch browsers unless told. |
| 6.3 | Architecture section with diagram | The "LLM as sensor" thesis stated up front |
| 6.4 | 2-3 minute demo video, linked in the README | Insurance if the deploy hiccups during review |
| 6.5 | Repository tidy | No dead code, no stray scratch files, clean log |

---

## Phase 7 — Buffer and submission (Day 7)

| # | Step |
|---|---|
| 7.1 | Test on a browser and machine that are not yours -- the deployed HTTPS URL, mic permissions from a fresh profile, cold start included |
| 7.2 | Re-read the brief; tick every stated expectation |
| 7.3 | Confirm every `.env.example` variable is set on the hosting platform's own dashboard (`/api/health` on the live URL, not localhost) and that STT/LLM/TTS quota has headroom for several evaluators each running a few real conversations, not just the single-developer usage the free tier was tested against |
| 7.4 | Send the submission email **in the morning** |

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Groq free-tier limits (8k TPM / 200k TPD on the LLM) stall development | High | Add paid credit on Day 1. Whole-week spend is estimated under $5. |
| **Orpheus TTS has its own, much tighter free-tier limit (3600 TPD)**, separate from the LLM's | High -- confirmed live in step 3.2, already partly consumed by that step's own testing | Synthesise sparingly during development (the audio cache means production usage is far lighter than test sweeps); add paid credit before phase 3's voice UI work needs repeated real synthesis |
| **Submission-week evaluator load exceeds free-tier quota mid-review** -- several evaluators each running a few 2-3 minute conversations is a realistic multiple of any single tier's daily cap, and the confirmed 3600 TPD Orpheus ceiling above shows this is not hypothetical | High, once the demo link is actually shared | Before sending the submission email (7.4): move all three services (STT, LLM, TTS) to a paid-but-cheap tier for the review window, not just the LLM (this register's own "under $5" estimate above was a development-week estimate, not a multi-evaluator one). The response-template audio cache (3.2) means repeat phrases cost nothing after the first evaluator, which helps but does not eliminate the risk for the necessarily-unique parts of every conversation (localities, items). |
| Cold start makes the demo look broken | High | Explicit UI state built (4.3, `isSlowStart` in `App.tsx`); not yet observed against a real cold host, since that needs an actual deploy |
| A rate-limit retry can't help if the limit is a daily quota, not a short one | Resolved in 4.4 -- confirmed live in step 3.2 (a `RateLimitError` said "try again in 5h40m0s"; one immediate no-backoff retry cannot address that) | `app/retry.py` reads the real `Retry-After` header and only retries when it names a short wait; otherwise falls straight through to the existing degraded fallback across all three Groq-calling modules |
| A long spoken summary is tedious to listen to (not an Orpheus input-length problem -- step 3.2 found there is no enforced cap -- but a genuine UX one) | Medium | Sentence chunking for streamed playback; fall back to displaying the full written summary while speaking a condensed one |
| VAD cuts the user off mid-sentence | Medium -- confirmed live: a fixed RMS threshold does not survive the browser's own automatic gain control across devices/rooms | Adaptive noise floor (phase 3.5 retroactive fix), not a hand-tuned constant; still needs real-microphone confirmation this environment cannot provide |
| STT mangles Indian place names, especially less common local ones | Medium -- confirmed live: `_LOCALITY_PROMPT` measurably improves recognition (3/4 phrases in a controlled A/B) | Whisper `prompt` conditioning (phase 3.5 retroactive fix) alongside keeping `raw_text` verbatim; the prompt's own side effect (widened noise/silence hallucination, including a live-caught silent-corruption case) is mitigated but not eliminated -- see phase 3.5's write-up |
| Strict JSON schema rejected by Groq | Medium | Resolved in 2.1 before anything depends on it |
| Templates sound robotic | Medium | Acknowledgment composition + variant rotation + `suggested_reply` escape hatch |
| Evaluator's browser is not Chrome -- `MediaRecorder`/mic support is partial or different on Safari and Firefox, and an evaluator has no reason to think to switch | Medium | Test explicitly on Chrome (test-plan.md's manual checklist already says so); state the tested browser plainly in the README (6.2) rather than silently assuming a reviewer already knows, and note whether other browsers were tried at all |
| A deployed key/setting works locally but not on the host -- an env var set in the local `.env` was never set on the hosting platform's own dashboard, the single most common way a deployed demo silently fails | Medium | `/api/health`'s `llm_configured` field exists precisely to make this checkable on the live URL itself (4.1); check it immediately after every deploy, not just once |
| Scope creep | High | This document. Anything not listed here is out of scope. |

---

## Out of scope

Recorded so the decision is not silently revisited: user accounts, booking persistence,
price estimation, maps/geocoding, contact-number capture, multi-language, streaming STT,
full-duplex audio, mobile-responsive polish, and any database.
