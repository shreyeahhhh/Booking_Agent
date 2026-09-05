# Design

The domain design: what the agent collects, how it decides, and what contract it holds
the language model to. For the runtime structure and data flow, see
[`architecture.md`](architecture.md). For the build order, see
[`../MASTER_PLAN.md`](../MASTER_PLAN.md).

---

## 1. Purpose and scope

A voice agent that gathers intra-city moving and transportation requirements through
natural conversation and produces a structured, confirmed booking summary.

### Provenance of every decision in this document

The brief deliberately withholds the field list and conversation flow — *"We are
intentionally not providing a predefined conversation flow or list of fields. We want to
see how you approach the problem."* So it matters which decisions came from where:

| Source | Meaning |
|---|---|
| **[REQUIRED]** | Stated explicitly in the assessment brief |
| **[INFERRED]** | Reasoned from the brief's example and the Porter-style framing |
| **[CHOSEN]** | Our own engineering decision, defensible but not mandated |

Nothing in this document asserts anything about Repatria's internal systems. The company
brief describes a booking agent; the domain modelling below is our own, informed by how
intra-city goods transport works in general.

**[REQUIRED]** by the brief: voice interaction; natural non-form conversation; intent
understanding; missing-information detection; ambiguity handling; context retention;
correction handling; order-independent input; a structured final summary; user review and
confirmation; secure server-side credentials; a deployed demo.

**[INFERRED]**: the domain is *intra-city* goods transport. The brief's example is
"Koramangala to Whitefield" — two localities within Bengaluru. Inter-city is supported but
treated as the secondary path.

**[CHOSEN]**: the specific field set, requirement classes, LLM/code split, template-based
responses, and the single-vendor Groq stack.

---

## 2. Requirements traceability

Each expectation in the brief, and the mechanism that satisfies it. This table is the
answer to "how do you know you have met the requirements?"

| Brief expectation | Mechanism | Module | Verified by |
|---|---|---|---|
| Understand requirements through voice | STT → extractor → state | `services/stt.py`, `llm/extractor.py` | Manual voice matrix |
| Natural, conversational interaction | Composed acknowledgment + rotated question variants | `conversation/templates.py` | Manual voice matrix |
| Ask relevant follow-up questions | Priority policy over unfilled slots | `domain/policy.py` | `test_policy.py` |
| Identify missing information | Declarative `FieldSpec` table + completeness engine | `domain/specs.py`, `domain/completeness.py` | `test_completeness.py` |
| Identify ambiguous information | Three independent detectors (model, validator, conflict) | `llm/schema.py`, `domain/reducer.py` | `test_ambiguity.py` |
| Remember earlier information | Typed state is the source of truth; the model never owns it | `domain/state.py` | `test_reducer.py` |
| Handle information in any order | Patches are field-addressed, not sequence-addressed | `domain/reducer.py` | `test_out_of_order.py` |
| Handle corrections | `op: correct` + revision history + cascade invalidation | `domain/reducer.py` | `test_corrections.py` |
| Avoid re-asking answered questions | Policy reads state, never history — filled slots are unaskable | `domain/policy.py` | `test_no_repeat.py` |
| Determine sufficiency | `can_enter_review()` guard, computed not judged | `conversation/machine.py` | `test_machine.py` |
| Structured final summary | Deterministic renderer over state | `conversation/summary.py` | `test_summary.py` |
| Review and confirm | `REVIEW ⇄ CORRECTING` phases | `conversation/machine.py` | Golden conversations |
| Credentials not exposed to client | Single server-side `GROQ_API_KEY`; browser never calls a vendor | `app/config.py` | Code review |
| Error handling | Timeout + retry + degraded fallback on every external call | `services/`, `llm/` | `test_failures.py` |

---

## 3. Booking schema

### 3.1 Field provenance wrapper

Every field carries how it was learned, not just what it is. This is what makes
corrections, ambiguity and confidence auditable — and it is what the live state panel
renders.

```python
class FieldStatus(StrEnum):
    EMPTY     = "empty"      # never mentioned
    INFERRED  = "inferred"   # derived by code, awaiting confirmation
    PROVIDED  = "provided"   # user stated it
    AMBIGUOUS = "ambiguous"  # stated but not actionable
    CONFIRMED = "confirmed"  # user explicitly affirmed it

class Field[T]:
    value:      T | None
    status:     FieldStatus
    confidence: float          # 0.0 - 1.0
    evidence:   str | None     # verbatim quote from the user
    turn:       int | None     # when it was captured
    revisions:  list[Revision] # full correction history
```

`AMBIGUOUS` is the load-bearing state. A value can be *present* and still not count as
*filled* — which is how "Kochi" gets challenged instead of silently accepted.

### 3.2 Structure

```
BookingState
├── booking_type : BookingType                    [INFERRED]
├── pickup       : Address
├── drop         : Address
├── schedule     : Schedule
├── goods        : Goods
├── service      : Service
├── notes        : list[Note]                     [OPTIONAL]
└── assumptions  : list[Assumption]               system-generated

Address   : raw_text, locality, city, landmark, floor, has_lift
Schedule  : date, time_window, exact_time, is_asap
Goods     : category, items[], load_hint
Item      : name, quantity, size_hint
Service   : vehicle_type, helpers_required, needs_packing, needs_disassembly
```

Enumerations:

| Enum | Values |
|---|---|
| `BookingType` | `house_shifting`, `single_item`, `bulk_goods`, `parcel` |
| `GoodsCategory` | `furniture`, `appliances`, `household_mixed`, `commercial_goods`, `parcel_documents`, `other` |
| `TimeWindow` | `morning` (06-12), `afternoon` (12-16), `evening` (16-20), `night` (20-24) |
| `VehicleType` | `two_wheeler`, `three_wheeler`, `tata_ace`, `pickup_8ft`, `tempo_14ft` |
| `SizeHint` | `small`, `medium`, `large` |
| `AmbiguityReason` | `city_level_only`, `vague_location`, `relative_date`, `vague_time`, `vague_quantity`, `unknown_item`, `conflicting` |

### 3.3 Requirement classes

The core design judgement. Each field is one of four kinds:

| Class | Behaviour |
|---|---|
| **Required** | Always blocks completion |
| **Conditionally required** | Requirement is a predicate over current state, re-evaluated every turn |
| **Inferred** | Derived by code from other fields; *proposed for confirmation*, never asked cold |
| **Optional** | Accepted if offered, never asked |

| Priority | Field | Class | Condition / note |
|---|---|---|---|
| 10 | `pickup.locality` | Required | |
| 20 | `drop.locality` | Required | |
| 30 | `goods.category` | Required | Usually inferred from the opening utterance |
| 40 | `goods.items` | Conditional | Required unless `booking_type == parcel` |
| 50 | `schedule.date` | Required | |
| 55 | `schedule.time_window` | Required | Satisfied by `is_asap` |
| 60 | `pickup.floor` | Conditional | Only if goods are furniture / appliances / household |
| 65 | `pickup.has_lift` | Conditional | **Only if `floor >= 2`** |
| 70 | `drop.floor` | Conditional | As above |
| 75 | `drop.has_lift` | Conditional | **Only if `floor >= 2`** |
| 80 | `service.needs_disassembly` | Conditional | Only if items include bulky furniture (bed, wardrobe) |
| 85 | `service.needs_packing` | Conditional | Only if `booking_type == house_shifting` |
| 90 | `service.vehicle_type` | Inferred | Proposed from items, then confirmed |
| 95 | `service.helpers_required` | Inferred | Proposed from item weight and floors |
| — | `*.landmark`, `schedule.exact_time`, `notes` | Optional | Never asked |

### 3.4 Rationale for the notable choices

**Locations before goods before time before logistics.** Priority ordering follows how
people actually think about a move. Asking "which floor?" before knowing what is being
moved is the rigid-form behaviour the brief explicitly contrasts against.

**`vehicle_type` is inferred, then confirmed — never asked cold.** A user moving a sofa
does not know what a Tata Ace is. Asking "what vehicle do you want?" is a form; saying
*"a Tata Ace should handle that — sound right?"* is an assistant. This single behaviour is
the clearest available demonstration of intent understanding.

**`has_lift` is gated on `floor >= 2`, not `floor > 0`.** A lift is irrelevant for a
ground- or first-floor move. Asking anyway is exactly the kind of mechanical questioning
that makes an agent feel like a form.

**`raw_text` is retained alongside the normalised `locality`.** STT will mangle Indian
place names. Keeping what was actually heard means a mis-transcription is recoverable and
visible, rather than silently overwritten by a confident-looking normalisation.

**`assumptions[]` is part of the state.** When clarification is exhausted (see §5.4), the
agent records what it assumed and surfaces it in the final summary. An agent that says
what it guessed is more trustworthy than one that guesses silently.

### 3.5 Deliberately excluded

| Excluded | Why |
|---|---|
| Contact name / phone | Capturing digits over voice is an STT accuracy problem, not a conversation-design problem. It adds failure modes while demonstrating nothing the brief tests. |
| Geocoding, pincode, lat/long | No maps integration. Localities are captured as text. |
| Price estimation | Requires a rate card we do not have; inventing one would be fabrication. |
| Payment, accounts, booking history | Out of scope for a requirement-gathering demo. |

---

## 4. LLM contract

### 4.1 What the model is allowed to do

Convert **one utterance** into proposed field changes. That is the entire job. It does not
hold state, choose questions, decide completion, resolve dates, or write user-facing text
(with the single bounded exception in §4.4).

### 4.2 Structured output

Pydantic model → JSON Schema → Groq `response_format: {type: "json_schema", strict: true}`.
`strict` mode uses constrained decoding, so the output is **guaranteed** schema-valid.

```jsonc
{
  "intent": "provide_info | correction | confirm | reject | question | off_topic | unclear",
  "patches": [
    {
      "op": "set | correct | append | remove | clear",
      "field": "pickup.locality",
      "value": "Koramangala",
      "previous_value": null,      // required for op: correct
      "confidence": 0.95,
      "evidence": "from Koramangala",
      "needs_normalization": false, // dates, times, quantities
      "ambiguity": null             // AmbiguityReason enum
    }
  ],
  "unresolved_mentions": [],
  "suggested_reply": null
}
```

Note that `strict` mode requires every property to be present and
`additionalProperties: false`; optional fields are expressed as `anyOf: [T, null]` rather
than omitted. Step 2.1 validates the generated schema against Groq before anything depends
on it.

### 4.3 System prompt

Held as a file (`llm/prompts/extractor.md`), not a string literal, so it can be diffed and
reviewed like code. Kept short — every token is billed on every call, and short prompts are
followed more reliably.

```
You are the language-understanding component of a logistics booking assistant.
You do NOT talk to the user. You do NOT ask questions. You do NOT write replies.
Your only job is to read the user's latest utterance and output structured field updates.

INPUT
  CURRENT_STATE  - booking fields already known (non-empty fields only)
  LAST_QUESTION  - what the assistant just asked, or null
  RECENT_TURNS   - the last 4 exchanges

RULES
1. Extract only what the user actually said. Never infer, guess, or fill plausible values.
2. `evidence` must be a verbatim substring of the utterance. If you cannot quote it,
   do not emit the patch.
3. Do NOT resolve dates or times. Emit the raw phrase ("tomorrow", "this Saturday")
   and set needs_normalization: true. The backend resolves them.
4. If the user changes a value already in CURRENT_STATE, use op "correct" and set
   previous_value to the value being replaced.
5. If a value is too vague to act on, still emit the patch, set `ambiguity` to the
   reason, and set confidence <= 0.5.
6. One utterance may contain several facts. Emit one patch per fact.
7. Anything you cannot map to a field goes in unresolved_mentions.
8. Never emit a patch for a field the user did not mention in THIS utterance,
   even if that field is missing from CURRENT_STATE.

CONFIDENCE
  1.0        explicit and unambiguous
  0.7 - 0.9  clear but indirect
  0.4 - 0.6  vague, or inferred from phrasing
  below 0.4  do not emit the patch
```

**Rule 8 is the most important line in the file.** Without it a helpful model will "assist"
by filling gaps it was never told about — precisely the hallucinated-detail failure mode.
**Rule 3 is second**: never let a language model do calendar arithmetic.

### 4.4 The one exception: `suggested_reply`

Templates cover the enumerable conversation states. They cannot cover a user asking
"do you also do packing?" or saying something unparseable. For those turns only — where
`intent` is `question`, `off_topic` or `unclear` — the extractor may return a
`suggested_reply` string, which the responder uses in place of a template.

This costs **zero extra calls** (the model is already being invoked on that turn) and
removes the "robotic when surprised" failure mode on the top-graded criterion. It is a
one-line cut if a stricter "no LLM-generated user-facing text" claim is preferred.

---

## 5. Conversation design

### 5.1 Phases

```
GREETING -> GATHERING -> CONFIRM_INFERRED -> REVIEW -> COMPLETE
                |   ^                          |  ^
                v   |                          v  |
            CLARIFYING                     CORRECTING
```

Every transition is a guard over state, never a model opinion:

```python
def can_enter_review(state) -> bool:
    return not missing(state) and not ambiguous(state) and not conflicts(state)
```

Premature completion is therefore **structurally impossible**.

### 5.2 Response composition

```
acknowledgment(state_diff) + [correction_note] + question(next_slot, state)
```

> "Got it — Koramangala to Whitefield, tomorrow evening. Which floor is the pickup on?"

The acknowledgment is generated from what actually changed this turn. This is what signals
retention to the user, and it is pure string assembly. Each slot has 3-4 phrasing variants,
rotated so nothing repeats verbatim within a conversation. Questions are slot-aware:
"Which floor in Koramangala?" beats "Which floor?".

### 5.3 Correction strategy

Three guarantees in the reducer:

1. **`op: set` cannot overwrite a `CONFIRMED` field.** It raises a conflict, which the
   policy turns into a clarification. Only `op: correct` may replace confirmed values.
2. **Replacements are recorded, not mutated away.** The old value is pushed to
   `revisions[]` and rendered as `Kakkanad (was: Kochi — corrected at turn 6)`.
3. **Cascade invalidation.** Changing a field resets everything derived from it. Correcting
   the item list flips `vehicle_type` from `INFERRED` back to `EMPTY` so it is re-inferred.
   Without this: the user adds a fridge and the agent still says "Tata Ace".

A correction made during `REVIEW` returns to `REVIEW` after revalidation — never to
`GATHERING`. Correcting one field must not restart requirement gathering.

### 5.4 Ambiguity strategy

Three independent detectors, so ambiguity does not depend on the model noticing:

| Detector | Example |
|---|---|
| Model-flagged (`ambiguity` enum) | "some furniture" → `vague_quantity` |
| Validator | `drop = "Kochi"` — a city with no locality → `city_level_only`; a resolved date in the past |
| Reducer conflict | new value contradicts a `CONFIRMED` field |

`AMBIGUOUS` counts as unfilled, so the policy raises it naturally. Clarification templates
are keyed by reason: `city_level_only` → *"Which part of Kochi — any area or landmark?"*

**Bounded retries: two attempts per field.** After that the best available value is
accepted, an `Assumption` is recorded, and it appears in the final summary. This prevents
the infinite-clarification loop that makes voice demos unwatchable, and turns a failure
into a visible, honest disclosure.

### 5.5 Redundant-question prevention

Structural, not defensive: **the policy reads state, never conversation history.** It
selects the highest-priority slot whose status is `EMPTY` or `AMBIGUOUS`. A filled slot is
*unaskable* — not "we remember not to ask", but "there is no code path that can ask".

Supporting guards: `asked_count` per slot (skipped at 2), never the same slot twice
consecutively outside a bounded clarification, and acknowledgment prefixes that let the
user *hear* retention working.

### 5.6 Final summary

Generated deterministically from state. Structure:

```
Pickup      Koramangala, 3rd floor, lift available
Drop        Whitefield, ground floor
Date/time   Saturday 12 September, evening (4-8pm)
Items       1 sofa, 3 cupboards
Vehicle     Tata Ace (suggested)
Helpers     2
Notes       Fragile items - handle with care
Assumed     Time window taken as evening; exact time not specified
```

Spoken in condensed form (Orpheus caps input at 200 characters per request, so it is
chunked by sentence); displayed in full on screen.

---

## 6. Failure modes and mitigations

The specific ways LLM agents break, and where each is addressed:

| Failure mode | Mitigation | Where |
|---|---|---|
| Hallucinating booking details | Rule 8 + mandatory verbatim `evidence` + confidence floor | `llm/prompts/`, `domain/reducer.py` |
| Overwriting correct information | `CONFIRMED` fields reject `op: set`; only `op: correct` replaces | `domain/reducer.py` |
| Asking redundant questions | Policy reads state, not history | `domain/policy.py` |
| Losing context | Typed state is the source of truth; bounded 4-turn window prevents drift | `domain/state.py` |
| Misunderstanding corrections | Explicit `op: correct` with `previous_value` + revision log | `domain/reducer.py` |
| Producing invalid structured data | `strict: true` constrained decoding + Pydantic validation + one repair pass | `llm/schema.py`, `llm/extractor.py` |
| Treating ambiguity as certainty | Three independent detectors; `AMBIGUOUS` counts as unfilled | §5.4 |
| Premature completion | `can_enter_review()` is computed, not judged | `conversation/machine.py` |
| Calendar arithmetic errors | The model never resolves dates; `dateutil` does | `domain/normalizers.py` |
| Infinite clarification loops | Two attempts per field, then a recorded assumption | `domain/policy.py` |

**Note on constrained decoding:** `strict: true` guarantees the *shape*, never the
*semantics*. A schema-valid patch can still set `pickup` to a drop-off address or a date in
the past. The reducer validates contents regardless — the guarantee removes one failure
class, not the need for validation.
