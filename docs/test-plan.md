# Test Plan

Four layers, cheapest first. Layers 1 and 4 run on every change; layer 2 runs whenever the
prompt or model changes; layer 3 is the regression net.

| Layer | What | Cost | Speed |
|---|---|---|---|
| 1. Unit | Deterministic core, no network | Free | Milliseconds |
| 2. Extraction eval | Scored `(state, utterance) → patches` cases | ~40 LLM calls | ~30s |
| 3. Golden conversations | Full transcripts, LLM responses recorded and replayed | Free after first record | Seconds |
| 4. Fast-path safety | Adversarial corpus; asserts fast paths *decline* | Free | Milliseconds |

Plus a **manual voice checklist** for what automation cannot catch.

---

## Layer 1 — Unit tests (no LLM)

- `test_state.py` — field provenance, JSON round-trip
- `test_specs.py` — conditional requirement predicates across every branch
- `test_normalizers.py` — relative dates, time windows, quantities
- `test_inference.py` — vehicle and helper lookup table
- `test_reducer.py` — write rules, `CONFIRMED` overwrite rejection, revisions, cascade invalidation
- `test_completeness.py` — `missing[]` / `ambiguous[]` / `conflicts[]`
- `test_policy.py` — priority ordering, no-repeat guarantee, bounded clarification
- `test_machine.py` — phase transitions, `can_enter_review` guard
- `test_summary.py` — deterministic summary rendering
- `test_import_boundary.py` — **`domain/` must not import `llm/` or `services/`**

### Date normalisation edge cases (these are where bugs actually live)

| Input | Session time | Expected |
|---|---|---|
| "tomorrow" | Fri 11 Sep | Sat 12 Sep |
| "this Saturday" | Fri 11 Sep | Sat 12 Sep |
| **"Saturday"** | **Sat 12 Sep** | **Sat 19 Sep** (the *coming* Saturday, not today) |
| "next Friday" | Fri 11 Sep | Fri 18 Sep |
| "day after tomorrow" | Fri 11 Sep | Sun 13 Sep |
| "tonight" | Fri 11 Sep 22:00 | Fri 11 Sep, `night` |
| "morning" | any | `TimeWindow.MORNING` |
| "half past four" | any | 16:30, `afternoon` |
| "the 30th" | Feb | Reject — invalid date, ask again |

---

## Layer 2 — Extraction eval set

~40 hand-written cases of `(current_state, last_question, utterance) → expected patches`,
scored on precision and recall over **field**, **value** and **op** independently.

This is what makes the model choice defensible: run it against `gpt-oss-120b` and
`gpt-oss-20b` and pick on measurement rather than assumption (step 5.5).

Re-run whenever the prompt changes. Never tune a prompt without a score in front of you.

---

## Layer 3 — Golden conversations

Full turn-by-turn transcripts asserted against expected final state. LLM responses are
**recorded to disk on first run and replayed thereafter** — deterministic, fast, and free,
so the suite can run on every commit.

### Scenario matrix

| # | Scenario | Opening / key turn | Expected agent behaviour |
|---|---|---|---|
| 1 | **Normal booking** | "I need to move a sofa from Koramangala to Whitefield tomorrow evening" | Captures pickup, drop, item, date, time in one turn. Asks only about floors next — never re-asks the four it already has. |
| 2 | **Information in random order** | "Third floor, no lift. It's a fridge. Kakkanad to Edappally. Saturday." | All five facts land in the correct fields regardless of order. |
| 3 | **Missing information** | "I need to move some stuff" | Asks pickup first (priority 10), one question at a time — not a barrage. |
| 4 | **Ambiguous location** | "...to Kochi" | Flags `city_level_only`; asks "which part of Kochi?" rather than accepting a city as a drop point. |
| 5 | **Ambiguous quantity** | "a few boxes" | Flags `vague_quantity`; asks roughly how many. Does **not** invent a number. |
| 6 | **Ambiguous time** | "sometime Saturday" | Accepts the date, flags the time as vague, asks for a window. |
| 7 | **Single correction** | "Actually, not tomorrow. Saturday." | `schedule.date` becomes Saturday. Revision recorded. Nothing else changes. |
| 8 | **Quantity correction** | "It's three cupboards, not two." | Quantity → 3. **Cascade:** inferred vehicle re-evaluated. |
| 9 | **Location refinement** | "The destination isn't Kochi city, it's Kakkanad." | `drop.locality` → Kakkanad; ambiguity cleared. |
| 10 | **Multiple corrections in one turn** | "Make it Saturday, and it's three cupboards not two" | Both applied; both recorded. |
| 11 | **Contradictory information** | Confirms 3rd floor, later says "ground floor" | Does not silently overwrite a `CONFIRMED` field — raises a conflict and asks which is right. |
| 12 | **Unnecessary information** | "My landlord is being difficult about the deposit" | Goes to `unresolved_mentions`; does not become a booking field. Agent does not derail. |
| 13 | **User changes their mind** | "Actually, let's do the whole flat, not just the sofa" | `booking_type` changes; newly-required fields (packing, more floors) appear and are asked. |
| 14 | **Incomplete answer** | Q: "Which floor and is there a lift?" A: "Third floor." | Records the floor, asks only about the lift — not both again. |
| 15 | **Confirmation** | "Yes, that's right" | Fast path, **zero LLM calls**. Transitions to `COMPLETE`. |
| 16 | **Rejection at summary** | "No, the date is wrong" | Enters `CORRECTING`, asks what to change, returns to `REVIEW` — does not restart gathering. |
| 17 | **Conditional confirm** | "Yes but change the time to 4pm" | Not a clean yes/no — escalates to the LLM, applies the correction, re-presents. |
| 18 | **Natural speech** | "Umm, so, yeah, I guess I need to, like, move a couple of things" | Handles fillers; extracts the intent without treating "couple" as exact. |
| 19 | **Premature completion attempt** | User says "that's everything" with fields missing | Refuses to finish; names what is still needed. **Structurally guaranteed.** |
| 20 | **Repeated clarification** | Twice fails to resolve a vague item | Stops asking after 2 attempts, records an assumption, surfaces it in the summary. |

---

## Layer 4 — Fast-path safety

The one way this architecture can silently corrupt state is a fast path matching when it
should not. Test it adversarially: assert the classifier **declines** to match on inputs
that superficially look trivial.

| Utterance | Last question | Must NOT fast-path because |
|---|---|---|
| "third floor, but there's no lift" | "Which floor?" | Carries a second fact |
| "no, wait, make it three" | "Is that correct?" | A correction, not a rejection |
| "yes, and also add a fridge" | "Is that correct?" | Affirmation plus new information |
| "yeah but not Kochi" | "Is that correct?" | Affirmation plus a correction |
| "no lift" | "Which floor?" | Answers a different slot than the one asked |
| "2" | (no prior question) | No expected answer type |

---

## Manual voice checklist

What automation cannot catch. Run before submission, and after any VAD or TTS change.

- [ ] Mic permission prompt on a fresh browser profile
- [ ] VAD does not cut off mid-sentence on slow, considered speech
- [ ] VAD does not hang for seconds after the user stops
- [ ] Barge-in / interruption behaviour is not jarring
- [ ] TTS pronunciation of Indian place names is acceptable
- [ ] Summary readback is not tediously long when spoken
- [ ] Turn latency feels conversational (target under ~2.5s)
- [ ] Fast-path turns feel instant
- [ ] Works in Chrome; degrades acceptably elsewhere
- [ ] Works on the deployed HTTPS origin, not just localhost
- [ ] Behaves sanely with no speech at all (silence, background noise)
- [ ] Behaves sanely when the network drops mid-turn
