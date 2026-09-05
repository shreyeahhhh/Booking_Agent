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
| 2.5 | Conversation state machine | `conversation/machine.py` | Phase transitions guarded by pure predicates. `can_enter_review` requires zero missing, ambiguous and conflicting. |
| 2.6 | Deterministic summary renderer | `conversation/summary.py` | Final summary generated from state, **not** written by the model. Includes assumptions and correction history. |
| 2.7 | Text REPL harness | `tests/repl.py` | Type a conversation in the terminal, watch state fill. |

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

---

## Phase 3 — Voice (Day 3)

**Goal:** the same conversation, spoken.

| # | Step | Files | Acceptance |
|---|---|---|---|
| 3.1 | STT service | `services/stt.py` | Groq `whisper-large-v3-turbo`. Empty/noise transcripts handled without an LLM call. |
| 3.2 | TTS service + audio cache | `services/tts.py` | Groq Orpheus. Sentence chunking for the 200-char limit. Cache keyed by text hash. Browser `speechSynthesis` fallback flag. |
| 3.3 | Fast-path classifier | `conversation/fastpath.py` | Yes/no, meta-commands, bare numerics. **Fails open to the LLM.** |
| 3.4 | `/turn` endpoint | `api/routes.py` | Orchestrates STT → fastpath/extract → reduce → policy → template → TTS. |
| 3.5 | Voice UI | `frontend/src/` | Mic button, VAD silence detection (~700ms), audio playback, transcript. |
| 3.6 | Live state panel | `frontend/src/` | Fields fill as they are captured; corrections render as `Kakkanad (was: Kochi)`. This is the evidence exhibit for the whole architecture. |

**Acceptance:** a complete booking spoken end to end on localhost, under ~2.5s per turn.

---

## Phase 4 — Deployment and resilience (Day 4)

**Deploy first thing in the morning, before any polish.**

| # | Step | Acceptance |
|---|---|---|
| 4.1 | Single-service deploy (FastAPI serves the built frontend bundle) | Public HTTPS URL loads the app |
| 4.2 | Microphone works on the deployed origin | Verified in a fresh browser profile |
| 4.3 | Cold-start mitigation | Either a warm instance or an explicit "waking up" UI state |
| 4.4 | Error handling pass | Every external call has a timeout, a retry and a spoken degraded fallback |
| 4.5 | Session TTL sweep | Memory does not grow unbounded |

**Acceptance:** a link that a stranger can open and complete a booking through, and that
degrades gracefully when STT, the LLM or TTS fails.

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
| 6.2 | README: assumptions and limitations | Explicitly required by the brief |
| 6.3 | Architecture section with diagram | The "LLM as sensor" thesis stated up front |
| 6.4 | 2-3 minute demo video, linked in the README | Insurance if the deploy hiccups during review |
| 6.5 | Repository tidy | No dead code, no stray scratch files, clean log |

---

## Phase 7 — Buffer and submission (Day 7)

| # | Step |
|---|---|
| 7.1 | Test on a browser and machine that are not yours |
| 7.2 | Re-read the brief; tick every stated expectation |
| 7.3 | Send the submission email **in the morning** |

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Groq free-tier limits (8k TPM / 200k TPD) stall development | High | Add paid credit on Day 1. Whole-week spend is estimated under $5. |
| Cold start makes the demo look broken | High | Warm instance or explicit UI state (4.3) |
| Orpheus 200-char cap makes the summary clunky | Medium | Sentence chunking; fall back to displaying the full summary and speaking a condensed one |
| VAD cuts the user off mid-sentence | Medium | Tune by ear on real speech, not by theory |
| STT mangles Indian place names | Medium | Keep `raw_text` verbatim alongside the normalised locality; never discard what was heard |
| Strict JSON schema rejected by Groq | Medium | Resolved in 2.1 before anything depends on it |
| Templates sound robotic | Medium | Acknowledgment composition + variant rotation + `suggested_reply` escape hatch |
| Scope creep | High | This document. Anything not listed here is out of scope. |

---

## Out of scope

Recorded so the decision is not silently revisited: user accounts, booking persistence,
price estimation, maps/geocoding, contact-number capture, multi-language, streaming STT,
full-duplex audio, mobile-responsive polish, and any database.
