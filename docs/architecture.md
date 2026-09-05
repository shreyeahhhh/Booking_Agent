# Architecture

## Thesis

> **The LLM is a sensor, not a controller.**
>
> It converts a single user utterance into proposed, evidence-backed changes to a typed
> booking state. Everything else — validation, state ownership, completeness checking,
> question selection, flow control and summary generation — is deterministic
> application code.

The structured `BookingState` object is the single source of truth. The language model
never owns it, never edits it directly, and never decides when the booking is finished.

This is enforced structurally, not by convention:

- `app/domain/` contains the deterministic core and **must never import from
  `app/llm/` or `app/services/`**. There is a unit test that asserts this.
- The model's output is applied through one function (`reducer.apply`) which validates
  every proposed change before it touches state.

## Turn loop

```
Browser                         Backend (FastAPI)
-------                         -----------------
mic -> MediaRecorder
 + silence detection (VAD)
      | POST /turn {audio, session_id}
      v
                1. STT           Groq whisper-large-v3-turbo -> user_text
                2. FAST PATH?    deterministic classifier
                                   hit  -> skip to step 4  (0 LLM calls)
                                   miss -> step 3
                3. EXTRACTOR     Groq gpt-oss-120b, strict JSON schema
                                   -> ExtractionResult{intent, patches[]}
                4. REDUCER       validate -> normalise -> apply -> event log
                5. COMPLETENESS  pure fn -> missing[], ambiguous[], conflicts[]
                6. POLICY        pure fn -> next action + target slot
                7. RESPONDER     template render (NO LLM)
                8. TTS           cache hit? serve bytes
                                 cache miss? Groq Orpheus -> cache
      | <- {audio, agent_text, user_text, state, missing, phase}
      v
play audio + render live state panel
```

**At most one LLM call per turn, frequently zero.**

## Components

| Component | Module | Deterministic? | Responsibility |
|---|---|---|---|
| Booking state | `domain/state.py` | Yes | `BookingState` and `Field[T]` -- the typed schema every other component reads and writes |
| Field specification | `domain/specs.py` | Yes | Declarative `FieldSpec` table: priority, conditional-requirement predicates, answer types |
| Extractor | `llm/extractor.py` | No | Utterance -> `ExtractionResult` (patches with evidence + confidence) |
| Reducer | `domain/reducer.py` | Yes | Validate, normalise and apply patches; append to event log |
| Normalisers | `domain/normalizers.py` | Yes | Relative dates, time windows, quantities. **Never the LLM.** |
| Inference | `domain/inference.py` | Yes | Vehicle type and helper count from the item list |
| Completeness | `domain/completeness.py` | Yes | State -> `missing[]`, `ambiguous[]`, `conflicts[]` |
| Policy | `domain/policy.py` | Yes | Chooses the next slot to address |
| State machine | `conversation/machine.py` | Yes | Phase transitions, guarded by pure predicates |
| Templates | `conversation/templates.py` | Yes | All user-facing text |
| Fast path | `conversation/fastpath.py` | Yes | Handles trivial turns without an LLM call |
| Session store | `session/store.py` | Yes | In-memory dict + TTL sweep |

## Conversation state machine

```
GREETING
  -> (first utterance) -> GATHERING

GATHERING
  -> CLARIFYING          when ambiguous[] is non-empty
  -> CONFIRM_INFERRED    when missing[] == 0 and ambiguous[] == 0
CLARIFYING
  -> GATHERING           once resolved
CONFIRM_INFERRED
  -> REVIEW
REVIEW
  -> COMPLETE            on affirmative
  -> CORRECTING          on negative / correction
CORRECTING
  -> REVIEW              after revalidation
```

The critical guard:

```python
def can_enter_review(state) -> bool:
    return not missing(state) and not ambiguous(state) and not conflicts(state)
```

Premature completion is therefore **structurally impossible** — there is no code path
by which the model can declare the booking finished.

`CORRECTING` returns to `REVIEW`, never to `GATHERING`: a correction made at the summary
stage must not restart requirement gathering.

## When the LLM is called

Exactly one trigger: a user utterance the fast-path classifier did not confidently
handle. In practice:

- any free-form utterance in `GATHERING`, `CLARIFYING` or `CORRECTING`
- a `REVIEW` utterance that is not a clean yes/no ("yes but change the time to 4pm")
- any utterance carrying multiple facts, a correction, or vague language

One call, one response. No chaining, no agent loop, no reflection pass.

## When the LLM is NOT called

| Situation | Handled by |
|---|---|
| **Generating any agent utterance** | Template renderer |
| **The final booking summary** | Deterministic serialiser over state |
| Yes/no after a binary question (<= 3 tokens, lexicon match) | Lexicon matcher |
| Empty / noise / silence from STT | Fixed re-prompt |
| Meta commands ("repeat that", "start over") | Keyword intent |
| **Resolving relative dates** ("tomorrow", "this Saturday") | `dateutil` + session start time |
| **Inferring vehicle type and helper count** | Lookup table over items |
| **Deciding what is missing / what to ask next** | Completeness engine + policy |
| **All state transitions** | Guard functions |
| Bare numeric answer to a numeric slot | Tight regex, gated on expected answer type |

### Fast paths fail open

Every fast path requires all three of:

1. a known expected answer type from the last question,
2. a short utterance, and
3. an exact pattern match.

Any miss falls through to the LLM. Correctness is never traded for a saved call —
"third floor, but there's no lift" must not be swallowed by the numeric matcher.
`tests/unit/test_fastpath_safety.py` asserts the fast path *declines* to match on an
adversarial corpus.

## Keeping templated speech natural

Responses are composed, not canned:

```
acknowledgment(state_diff) + [correction_note] + question(next_slot, state)
```

- The acknowledgment is generated from what actually changed this turn
  ("Got it — Koramangala to Whitefield, tomorrow evening"), which is what signals
  retention to the user.
- 3-4 phrasing variants per slot, rotated, so nothing repeats verbatim.
- Questions are slot-aware: "Which floor in Koramangala?" beats "Which floor?".

**One escape hatch, at zero extra cost:** the extractor's structured output includes an
optional `suggested_reply`, populated only when `intent` is something templates cannot
cover (`question_about_service`, `off_topic`, `unclear`). The model is already being
called on that turn, so this costs one extra field, not one extra request.

## Corrections

Three guarantees enforced in the reducer:

1. **A `CONFIRMED` field cannot be overwritten by `op: set`.** Only `op: correct` may
   replace it; a `set` against a confirmed field raises a *conflict*, which the policy
   turns into a clarification. This is what prevents accidental overwrites.
2. **Every replacement is recorded, not mutated away** — the old value is pushed to
   `revisions[]` with turn number and evidence, and rendered in the UI as
   `Drop: Kakkanad (was: Kochi - corrected at turn 6)`.
3. **Cascade invalidation.** Changing a field resets everything derived from it.
   Correcting the item list flips `vehicle_type` from `INFERRED` back to `EMPTY` so it
   is re-inferred and re-confirmed. Without this: user adds a fridge, agent still says
   "Tata Ace".

## Ambiguity

Detected from three independent sources, so it does not depend on the model noticing:

| Source | Example |
|---|---|
| LLM-flagged (`ambiguity` enum) | "some furniture" -> `vague_quantity` |
| Validator | `drop = "Kochi"` is a city with no locality -> `city_level_only`; a resolved date in the past |
| Reducer conflict | new value contradicts a `CONFIRMED` field |

An `AMBIGUOUS` field counts as **unfilled** for completeness, so the policy raises it
naturally. Clarification templates are keyed by ambiguity reason.

**Bounded retries:** at most 2 clarification attempts per field, after which the best
available value is accepted with an `assumption` note that appears in the final summary.
This prevents the infinite-clarification loop that makes voice demos unwatchable.

## Preventing redundant questions

Structural, not defensive: **the policy reads state, never conversation history.**
It selects the highest-priority slot whose status is `EMPTY` or `AMBIGUOUS`. A filled
slot is therefore *unaskable* — not "we remember not to ask", but "there is no code path
that can ask".

Supporting guards:

- `asked_count: dict[slot, int]`; a slot at 2 attempts is skipped and noted
- the policy never selects the same slot twice consecutively unless it is a bounded
  clarification
- acknowledgment prefixes echo newly captured values, so the user *hears* retention working

## Latency budget

| Stage | Budget | Notes |
|---|---|---|
| VAD endpointing | 600-900ms | Largest controllable slice |
| Upload | 100-200ms | |
| STT (whisper-turbo) | 200-400ms | |
| LLM (~200 tok @ ~500 tok/s) | 300-500ms | **Zero on fast-path turns** |
| TTS first byte | 300-600ms | **Near-zero on cache hit** |
| **Total** | **~1.5-2.5s**, often under 1s | |

Mitigations, in order of value:

1. **Pre-synthesised template audio cache.** Because responses come from a finite set of
   templates, their audio is synthesised once and served from cache thereafter. This is
   the direct payoff of choosing templates over generated text: it cuts both latency and
   TTS spend (~10x) with the same decision.
2. **VAD threshold tuned to ~700ms** — biggest single win, one constant.
3. **Stream TTS, play the first chunk immediately.** Orpheus caps input at 200 characters
   per request, so responses are split by sentence regardless.
4. **Optimistic UI** — render the transcript and state-panel updates the moment STT
   returns, before audio is ready. Perceived latency matters more than real latency.

## Model selection

Primary: **`openai/gpt-oss-120b`** on Groq.

| Model | Verdict |
|---|---|
| **`openai/gpt-oss-120b`** | **Chosen.** Production tier. Strict structured outputs (server-side schema validation) mean a malformed generation fails loudly with an error rather than reaching the client as broken JSON — see docs/design.md §4.2 for what this does and does not guarantee in practice. ~500 tok/s. $0.15 / $0.60 per 1M tokens. Strong instruction following, which matters for the "never invent a value" rules. |
| `openai/gpt-oss-20b` | Benchmark candidate. ~1000 tok/s, half the price, also strict-capable. Likely weaker on correction detection and ambiguity judgement — to be measured on the extraction eval set, not assumed. |
| `llama-3.3-70b-versatile` | Rejected. No strict mode (best-effort JSON only) and slower at 280 tok/s. |
| `llama-3.1-8b-instant` | Rejected for extraction. Too weak for correction and ambiguity nuance. |
| Qwen preview models | Rejected on **stability**, not capability. Groq documents preview models as evaluation-only and subject to discontinuation; a submission that must stay live for reviewers cannot depend on one. |
| `groq/compound` | Rejected. Agentic system with web search and code execution — added latency and nondeterminism for no benefit. |

`reasoning_effort` is set to `low` on the extractor: this is mechanical extraction, not
deliberation, and reasoning tokens are billed as output.

## Deliberately excluded

| Not used | Why |
|---|---|
| Database | A booking conversation is one short-lived session with no cross-session reads. In-memory dict behind a swappable interface. |
| Serverless / function hosting (e.g. Vercel, Netlify Functions) | The brief names these as example platforms, but the in-memory session store above needs one warm process to persist across a conversation's turns, which a function platform gives no guarantee of. Phase 4 targets a persistent-process host instead (Render/Railway/Fly.io-class). |
| LangChain / agent frameworks | The abstraction would hide exactly the logic being assessed. |
| RAG / vector store | Nothing to retrieve. |
| Realtime speech-to-speech APIs | They put the model in charge of state, which contradicts the entire design. |
| Authentication | Out of scope for a demo. |
| Contact name / phone capture | Capturing digits over voice is an STT accuracy problem, not a conversation-design problem. It would add failure modes while demonstrating nothing the brief tests. |
| Geocoding / maps | No pincode or lat-long resolution; localities are captured as text. |
