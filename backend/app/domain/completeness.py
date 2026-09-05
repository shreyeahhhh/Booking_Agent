"""Turning the current state into missing / ambiguous / conflicting slots.

Every function here is a pure read over BookingState -- nothing here decides
what to do about the result, that is domain/policy.py's job. Keeping "what's
wrong with the state" (this module) separate from "what to do next" (policy)
means each half can be tested and reasoned about on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.specs import FIELD_SPECS, RequirementKind, get_field
from app.domain.state import AmbiguityReason, BookingState, Conflict, FieldStatus


@dataclass(frozen=True)
class AmbiguousField:
    field_path: str
    reason: AmbiguityReason | None
    value: object | None


def _is_filled(state: BookingState, field_path: str) -> bool:
    """Special-cased for goods.items, which is a list, not a Field[T].

    An empty list is unfilled; a non-empty one is filled. There is no
    "ambiguous list" concept -- ambiguity about a specific item's quantity
    lives on that item, handled at the conversation level, not here.
    """
    if field_path == "goods.items":
        return bool(state.goods.items)
    return get_field(state, field_path).is_settled


def missing(state: BookingState) -> list[str]:
    """Required or currently-conditionally-required fields that are unfilled.

    An AMBIGUOUS field counts as unfilled here -- see docs/design.md SS3.1:
    a value can be present and still not count as usable. INFERRED-kind specs
    are excluded on purpose: they are a separate completeness dimension (see
    `unconfirmed_inferred` below), because a value can only be inferred once
    something else already filled in enough to derive it from. Counting them
    as "missing" from the start would make completion impossible to reach.
    """
    return [
        spec.field_path
        for spec in FIELD_SPECS
        if spec.kind != RequirementKind.INFERRED
        and spec.is_required_now(state)
        and not _is_filled(state, spec.field_path)
    ]


def ambiguous(state: BookingState) -> list[AmbiguousField]:
    """Required or conditionally-required fields specifically flagged AMBIGUOUS.

    A strict subset of `missing()`: every ambiguous field is also missing, but
    not every missing field is ambiguous (most are just EMPTY). Kept separate
    because they need different handling -- a CLARIFYING question about *why*
    a value did not resolve, versus a plain GATHERING question for something
    never mentioned at all. Optional fields are excluded: if it is never
    asked about, an unclear answer for it is not worth blocking on either.
    """
    out = []
    for spec in FIELD_SPECS:
        if spec.kind == RequirementKind.INFERRED or spec.field_path == "goods.items":
            continue
        if not spec.is_required_now(state):
            continue
        f = get_field(state, spec.field_path)
        if f.status == FieldStatus.AMBIGUOUS:
            out.append(AmbiguousField(spec.field_path, f.ambiguity, f.value))
    return out


def conflicts(state: BookingState) -> list[Conflict]:
    """Pending contradictions between a CONFIRMED value and a later `set`.

    Simply reads `state.conflicts` -- conflicts are recorded directly onto the
    state by the reducer precisely so this can be a plain read, never a scan
    of conversation history. See docs/architecture.md, "Preventing redundant
    questions".
    """
    return list(state.conflicts)


def unconfirmed_inferred(state: BookingState) -> list[str]:
    """INFERRED-kind fields that currently hold a system guess awaiting confirmation.

    Only counts a field once it actually has a guess (status == INFERRED) --
    an INFERRED-kind spec whose field is still EMPTY has nothing to confirm
    yet, and a CONFIRMED one is already done.
    """
    return [
        spec.field_path
        for spec in FIELD_SPECS
        if spec.kind == RequirementKind.INFERRED
        and get_field(state, spec.field_path).status == FieldStatus.INFERRED
    ]


def can_enter_review(state: BookingState) -> bool:
    """The guard that makes premature completion structurally impossible.

    Deliberately does not check `unconfirmed_inferred`: confirming an inferred
    guess is a distinct phase (CONFIRM_INFERRED) that sits between GATHERING
    and REVIEW in the conversation state machine (phase 2), not a precondition
    baked into this lower-level check. See docs/architecture.md's phase
    diagram and docs/design.md SS5.1.
    """
    return not missing(state) and not ambiguous(state) and not conflicts(state)
