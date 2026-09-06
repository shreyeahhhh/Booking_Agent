"""The fast-path classifier -- MASTER_PLAN.md step 3.3.

Deterministic, zero-cost recognition of the small set of utterance shapes
that need no language model at all: a bare yes/no, a bare number, or a
handful of fixed meta-commands ("repeat that", "start over"). Everything
else -- and any doubt at all -- falls through to the LLM extractor. See
docs/architecture.md's "Fast paths fail open": correctness is never traded
for a saved call.

Two independent safety properties, not one, keep this from ever silently
corrupting state (docs/test-plan.md Layer 4's adversarial corpus is built
against exactly this):

1. Yes/no and meta-command matching is *exact string membership* against a
   small, curated phrase set after normalising case/punctuation/whitespace
   -- never a prefix or substring check. "yes, and also add a fridge"
   starts with "yes" but is not a member of the phrase set, so it correctly
   falls through instead of being mistaken for a clean affirmation.
2. A bare yes/no or number is only ever interpreted as *an answer to the
   field actually pending*, never guessed from the utterance alone. "no
   lift" answering "which floor?" is not in any phrase or numeric set this
   module would even consult for an INTEGER-typed field, so it falls
   through regardless of how plausible it looks in isolation.

What a bare "yes"/"no" *means* depends entirely on what was just asked --
this module does not track conversation history itself, it is handed
exactly the `SlotDecision` the caller already has from the previous turn
(`machine.TurnResult.decision`), the same value `docs/architecture.md`'s
"policy reads state, never history" principle already relies on elsewhere:

- No pending decision, or a `CONFIRM_INFERRED` one: the question itself was
  yes/no-shaped ("does this look right?", "a Tata Ace should handle that --
  sound right?"), so yes/no means CONFIRM/REJECT, never a field value.
- A `CONFLICT`, `AMBIGUOUS` or `MISSING` decision on a `BOOLEAN`-typed field:
  yes/no is the field's own value.
- The same, on an `INTEGER`-typed field: a bare digit string is the value.
- Any other answer type (TEXT/DATE/TIME_WINDOW/ENUM): no fast path exists
  for these -- interpreting them needs judgement this module deliberately
  does not have.

`CONFLICT` produces `op: correct` rather than `op: set`: the field is
already `CONFIRMED` (that is why it is a conflict at all), and `set` against
a `CONFIRMED` field is rejected by the reducer's own guard rather than
resolving anything -- see `domain/reducer.py`'s three correction rules.

Meta-commands bypass the booking pipeline entirely rather than producing an
`ExtractionResult`: they are not booking facts, so there is nothing for the
reducer, policy or templates to do with them. The caller (the `/turn`
endpoint, step 3.4) is expected to handle `MetaCommand.REPEAT` by replaying
the previous response and `MetaCommand.RESTART` by resetting the
conversation -- this module only classifies, it does not perform either
action, the same separation `domain/reducer.py` keeps from `llm/extractor.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.conversation.machine import Phase
from app.domain.policy import SlotDecision, SlotReason
from app.domain.specs import AnswerType, spec_for
from app.domain.state import ExtractionResult, Intent, Patch, PatchOp

_YES_PHRASES = frozenset(
    {
        "yes",
        "yeah",
        "yep",
        "yup",
        "correct",
        "right",
        "sure",
        "ok",
        "okay",
        "confirmed",
        "yes please",
        "that's right",
        "that's correct",
        "sounds good",
    }
)
_NO_PHRASES = frozenset(
    {
        "no",
        "nope",
        "nah",
        "negative",
        "incorrect",
        "wrong",
        "not correct",
    }
)
_REPEAT_PHRASES = frozenset(
    {
        "repeat",
        "repeat that",
        "say again",
        "say that again",
        "can you repeat that",
        "what did you say",
        "come again",
        "pardon",
        "sorry what",
    }
)
_RESTART_PHRASES = frozenset(
    {
        "restart",
        "reset",
        "start over",
        "start again",
        "let's start over",
        "can we start over",
    }
)

_BARE_INTEGER = re.compile(r"\d+")
_PUNCTUATION = re.compile(r"[.!?,]")


class MetaCommand(StrEnum):
    REPEAT = "repeat"
    RESTART = "restart"


@dataclass(frozen=True)
class FastPathResult:
    """Exactly one of the two fields is set.

    `extraction`: feed through the normal reduce -> policy -> template
    pipeline, exactly as if the LLM had produced it. `meta_command`:
    bypass that pipeline -- the caller acts on it directly.
    """

    extraction: ExtractionResult | None = None
    meta_command: MetaCommand | None = None


def _normalize(text: str) -> str:
    stripped = _PUNCTUATION.sub("", text.strip().lower())
    return " ".join(stripped.split())


def _confirmation(intent: Intent) -> FastPathResult:
    return FastPathResult(extraction=ExtractionResult(intent=intent, patches=[]))


def _field_value(decision: SlotDecision, value: object, *, evidence: str) -> FastPathResult:
    op = PatchOp.CORRECT if decision.reason == SlotReason.CONFLICT else PatchOp.SET
    patch = Patch(
        op=op,
        field=decision.field_path,
        value=value,
        confidence=1.0,
        evidence=evidence,
    )
    return FastPathResult(extraction=ExtractionResult(intent=Intent.PROVIDE_INFO, patches=[patch]))


def classify(
    utterance: str,
    *,
    phase: Phase,
    decision: SlotDecision | None,
) -> FastPathResult | None:
    """Attempt to resolve one utterance without the LLM. None means "no
    confident match -- ask the extractor instead", never a guess."""
    normalized = _normalize(utterance)
    if not normalized:
        return None

    if normalized in _REPEAT_PHRASES:
        return FastPathResult(meta_command=MetaCommand.REPEAT)
    if normalized in _RESTART_PHRASES:
        return FastPathResult(meta_command=MetaCommand.RESTART)

    if phase == Phase.GREETING:
        # The very first turn of a session has no pending question at all --
        # not even the "no decision means a yes/no confirmation" case below,
        # which specifically means REVIEW/COMPLETE (see machine.TurnResult's
        # own docstring: decision is None *exactly* when the phase is REVIEW
        # or COMPLETE). A bare "yes" opening a conversation means nothing.
        return None

    if decision is None or decision.reason == SlotReason.CONFIRM_INFERRED:
        # The pending question is itself yes/no-shaped: either the
        # whole-booking summary (REVIEW/COMPLETE) or an inferred guess
        # awaiting confirmation. Either way, yes/no means confirm/reject,
        # never a value for whatever field decision.field_path names.
        if normalized in _YES_PHRASES:
            return _confirmation(Intent.CONFIRM)
        if normalized in _NO_PHRASES:
            return _confirmation(Intent.REJECT)
        return None

    # decision.reason is CONFLICT, AMBIGUOUS or MISSING: a concrete field is
    # pending, and a bare yes/no or number -- if the field's own answer type
    # matches -- is that field's value, not a confirmation of anything.
    spec = spec_for(decision.field_path)
    if spec.answer_type == AnswerType.BOOLEAN:
        if normalized in _YES_PHRASES:
            return _field_value(decision, True, evidence=utterance)
        if normalized in _NO_PHRASES:
            return _field_value(decision, False, evidence=utterance)
        return None

    if spec.answer_type == AnswerType.INTEGER:
        if _BARE_INTEGER.fullmatch(normalized):
            return _field_value(decision, int(normalized), evidence=utterance)
        return None

    # TEXT / DATE / TIME_WINDOW / ENUM: no fast path. Dates and time windows
    # need normalisation, enums need real understanding of what the user
    # meant -- exactly the judgement this module deliberately does not have.
    return None
