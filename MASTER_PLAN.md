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
| 1.1 | Booking state schema with field provenance | `domain/state.py` | `Field[T]` carries value, status, confidence, evidence, turn, revisions. Round-trips through JSON. |
| 1.2 | Declarative field specification table | `domain/specs.py` | One table drives completeness, question priority and answer types. Conditional requirements are predicates over state. |
| 1.3 | Value normalisers | `domain/normalizers.py` | "tomorrow", "this Saturday", "next Friday", "morning", "half past four", "a couple" all resolve. **"Saturday" said *on* a Saturday resolves to the coming Saturday, not today.** |
| 1.4 | Vehicle and helper inference | `domain/inference.py` | Item list → vehicle type + helper count via a lookup table. Pure function, no AI. |
| 1.5 | Reducer with correction and conflict rules | `domain/reducer.py` | `op: set` against a `CONFIRMED` field is **rejected** as a conflict. `op: correct` replaces and pushes to `revisions[]`. Cascade invalidation resets derived fields. |
| 1.6 | Completeness engine | `domain/completeness.py` | Pure fn → `missing[]`, `ambiguous[]`, `conflicts[]`. `AMBIGUOUS` counts as unfilled. |
| 1.7 | Question policy | `domain/policy.py` | Selects the highest-priority unfilled slot. **Reads state only, never history** — a filled slot is unaskable. |
| 1.8 | Unit test suite | `tests/unit/` | Includes the import-boundary test asserting `domain/` never imports `llm/` or `services/`. |

**Acceptance for the phase:** a scripted test feeds patches in scrambled order, applies
two corrections and one contradiction, and asserts the exact final state — with no network
calls and no API key set.

---

## Phase 2 — Language understanding (Day 2)

**Goal:** a complete, correct text conversation. Voice is deliberately deferred.

| # | Step | Files | Acceptance |
|---|---|---|---|
| 2.1 | Structured output contract | `llm/schema.py` | Pydantic → JSON Schema accepted by Groq with `strict: true` (all props required, `additionalProperties: false`, optionals as `anyOf[T, null]`). |
| 2.2 | Extractor prompt | `llm/prompts/extractor.md` | Prompt held as a file, not a string literal, so it can be diffed and reviewed. |
| 2.3 | Groq extractor client | `llm/extractor.py` | Timeout, one retry, and a repair pass that feeds validation errors back. Compact state serialisation (non-empty fields only) to keep prompts ~900 tokens. |
| 2.4 | Response templates | `conversation/templates.py` | `acknowledgment(diff) + [correction_note] + question(slot, state)`. 3-4 variants per slot, rotated. |
| 2.5 | Conversation state machine | `conversation/machine.py` | Phase transitions guarded by pure predicates. `can_enter_review` requires zero missing, ambiguous and conflicting. |
| 2.6 | Deterministic summary renderer | `conversation/summary.py` | Final summary generated from state, **not** written by the model. Includes assumptions and correction history. |
| 2.7 | Text REPL harness | `tests/repl.py` | Type a conversation in the terminal, watch state fill. |

**Acceptance:** a full booking completed by typing, with a correction mid-conversation and
a correction at the summary stage, producing a correct final summary.

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
