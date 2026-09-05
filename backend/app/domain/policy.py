"""Choosing what to address next.

This module reads state, never conversation history -- see
docs/architecture.md, "Preventing redundant questions". A filled slot is
*unaskable*: there is no code path here that can select a field whose status
already counts as filled, so "did we already ask that" is not something this
code has to remember, it is something the state already answers.

Priority, highest first:

1. An unresolved conflict -- a contradiction should be settled before any new
   information is gathered.
2. An ambiguous required/conditional field, provided it has not exhausted its
   clarification attempts.
3. A missing (empty) required/conditional field, in FieldSpec priority order.
4. An inferred field still awaiting confirmation.

Nothing left in any tier means there is nothing more to address.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain import completeness
from app.domain.specs import get_field, spec_for, with_field
from app.domain.state import Assumption, BookingState, Field, FieldStatus

DEFAULT_MAX_CLARIFY_ATTEMPTS = 2
# A plain constant, not read from app.config: domain/ must not depend on
# pydantic-settings or the environment (see tests/unit/test_import_boundary.py).
# Callers with access to configuration pass their own value through instead.


class SlotReason(StrEnum):
    CONFLICT = "conflict"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    CONFIRM_INFERRED = "confirm_inferred"


@dataclass(frozen=True)
class SlotDecision:
    field_path: str
    label: str
    reason: SlotReason


def sweep_and_select(
    state: BookingState, *, max_clarify_attempts: int = DEFAULT_MAX_CLARIFY_ATTEMPTS
) -> tuple[BookingState, SlotDecision | None]:
    """Resolve any exhausted ambiguities, then pick the next slot to address.

    Returns a (possibly updated) state together with the decision, rather than
    mutating in place, so it stays a plain, testable function of its inputs:
    call it once per turn and use both halves of the result.
    """
    state = _give_up_on_exhausted(state, max_clarify_attempts)

    for conflict in completeness.conflicts(state):
        spec = spec_for(conflict.field)
        return state, SlotDecision(conflict.field, spec.label, SlotReason.CONFLICT)

    for amb in completeness.ambiguous(state):
        spec = spec_for(amb.field_path)
        return state, SlotDecision(amb.field_path, spec.label, SlotReason.AMBIGUOUS)

    missing = sorted(completeness.missing(state), key=lambda path: spec_for(path).priority)
    if missing:
        spec = spec_for(missing[0])
        return state, SlotDecision(missing[0], spec.label, SlotReason.MISSING)

    for path in completeness.unconfirmed_inferred(state):
        spec = spec_for(path)
        return state, SlotDecision(path, spec.label, SlotReason.CONFIRM_INFERRED)

    return state, None


def record_question_asked(state: BookingState, field_path: str) -> BookingState:
    """Increment the clarification counter when an ambiguous field is asked about again.

    Only meaningful for AMBIGUOUS fields -- a plain missing field has no
    bounded-retry concept in this design; the agent simply keeps asking until
    it is answered. See docs/design.md SS5.4.
    """
    current = get_field(state, field_path)
    if current.status != FieldStatus.AMBIGUOUS:
        return state
    updated = current.model_copy(update={"clarify_attempts": current.clarify_attempts + 1})
    return with_field(state, field_path, updated)


def _give_up_on_exhausted(state: BookingState, max_clarify_attempts: int) -> BookingState:
    """Accept the best-known value for any ambiguous field past its retry cap.

    Without this, a field that never resolves would stay AMBIGUOUS -- which
    counts as unfilled -- forever, and the conversation could never reach
    REVIEW. Accepting it with a recorded assumption turns an unresolvable
    clarification into a visible, honest disclosure instead of an infinite
    loop. See docs/architecture.md, "Ambiguity".
    """
    for amb in completeness.ambiguous(state):
        field = get_field(state, amb.field_path)
        if field.clarify_attempts < max_clarify_attempts:
            continue
        spec = spec_for(amb.field_path)
        resolved: Field = field.model_copy(
            update={"status": FieldStatus.PROVIDED, "ambiguity": None}
        )
        state = with_field(state, amb.field_path, resolved)
        assumption = Assumption(
            field=amb.field_path,
            note=f"Could not fully clarify {spec.label}; kept as stated: {amb.value!r}.",
            turn=state.turn,
        )
        state = state.model_copy(update={"assumptions": [*state.assumptions, assumption]})
    return state
