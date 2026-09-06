"""The conversation state machine -- MASTER_PLAN.md step 2.5.

`domain.policy.sweep_and_select` already computes almost everything a phase
needs: its `SlotReason` (conflict / ambiguous / missing / confirm_inferred)
maps directly onto the GATHERING-side phases, and `decision is None` already
means "nothing left to gather." What that module does not and should not
know about is REVIEW and COMPLETE -- confirming or correcting the finished
booking is a conversation-level concern, not a completeness computation. This
module is the thin layer that adds exactly that on top, rather than
re-deriving anything domain/policy.py already computes correctly.

There is no separate CORRECTING phase in the enum below, even though
docs/architecture.md's diagram shows one. A correction made during REVIEW is
handled by the same general rule as every other turn: apply the patches,
then recompute. Most corrections (fixing a date, a quantity) leave the
booking still complete, so that recomputation naturally lands back on
REVIEW. But a scope-expanding correction ("actually, the whole flat, not
just the sofa" -- docs/test-plan.md scenario 13) can genuinely make new
fields required again, and that same recomputation just as naturally routes
back to GATHERING for them. A hard-coded "CORRECTING always returns to
REVIEW" rule would get that second, real case wrong; reusing the general
phase computation gets both right for free.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain import policy
from app.domain.policy import SlotDecision, SlotReason
from app.domain.reducer import apply, confirm_all
from app.domain.state import BookingState, ExtractionResult, Intent


class Phase(StrEnum):
    GREETING = "greeting"
    GATHERING = "gathering"
    CLARIFYING = "clarifying"
    CONFIRM_INFERRED = "confirm_inferred"
    REVIEW = "review"
    COMPLETE = "complete"


_REASON_TO_PHASE: dict[SlotReason, Phase] = {
    SlotReason.CONFLICT: Phase.CLARIFYING,
    SlotReason.AMBIGUOUS: Phase.CLARIFYING,
    SlotReason.MISSING: Phase.GATHERING,
    SlotReason.CONFIRM_INFERRED: Phase.CONFIRM_INFERRED,
}


@dataclass(frozen=True)
class ConversationState:
    booking: BookingState
    phase: Phase


@dataclass(frozen=True)
class TurnResult:
    conversation: ConversationState
    decision: SlotDecision | None  # None exactly when phase is REVIEW or COMPLETE


def start() -> ConversationState:
    return ConversationState(booking=BookingState(), phase=Phase.GREETING)


def advance(
    conversation: ConversationState,
    extraction: ExtractionResult,
    *,
    reference: datetime | None = None,
    max_clarify_attempts: int = policy.DEFAULT_MAX_CLARIFY_ATTEMPTS,
) -> TurnResult:
    """Process one turn and return the resulting conversation state.

    Reading `extraction.suggested_reply` to decide what to actually say is
    the caller's job, not this function's: the caller already has
    `extraction` in hand (it is what it passed in), so no extra plumbing is
    needed here just to make that value visible further up the call stack.
    This function only ever decides what *state* results from a turn.

    `max_clarify_attempts` exists so a caller with access to configuration
    (api/routes.py, from `settings.max_clarify_attempts`) can actually
    change the bounded-clarification threshold -- until step 3.4 wired this
    parameter through, `settings.max_clarify_attempts` was defined but read
    by nothing, the same orphaned-config shape step 2.2 found for
    `booking_type` and `schedule.is_asap`. Defaults to
    `policy.DEFAULT_MAX_CLARIFY_ATTEMPTS` so every existing caller
    (tests/repl.py, the phase 1/2 test suites) is unaffected.
    """
    booking = conversation.booking

    # A clean confirmation (no attached corrections) is the only case that
    # finalises anything -- see docs/architecture.md: "yes but change the
    # time to 4pm" is not a clean yes, so it falls through to the patch-apply
    # branch below and re-presents the summary rather than completing on it.
    is_clean_confirm = (
        conversation.phase in (Phase.CONFIRM_INFERRED, Phase.REVIEW, Phase.COMPLETE)
        and extraction.intent == Intent.CONFIRM
        and not extraction.patches
    )

    if is_clean_confirm:
        booking = confirm_all(booking)
    elif extraction.patches:
        booking = apply(booking, extraction.patches, reference=reference).state
    # else: no patches and not a clean confirm (a question, an off-topic
    # remark, or a bare rejection with no specifics yet) -- nothing about
    # the booking changes this turn.

    booking, decision = policy.sweep_and_select(booking, max_clarify_attempts=max_clarify_attempts)

    if decision is not None and decision.reason == SlotReason.AMBIGUOUS:
        # Record the ask *before* returning, since the decision returned
        # here is exactly what the caller will turn into a question next --
        # there is no code path where a computed decision is not asked.
        # Without this, domain/policy.py's bounded-retry give-up (tested in
        # isolation since phase 1) would never actually fire in a real
        # conversation: nothing else in the system calls it.
        booking = policy.record_question_asked(booking, decision.field_path)

    if decision is None:
        # Confirming the vehicle/helper guess (at CONFIRM_INFERRED) is not
        # the same act as confirming the whole booking (at REVIEW) -- only
        # the latter finalises anything. The former still routes to REVIEW,
        # so the user gets an explicit, separate holistic check before the
        # booking is treated as done.
        was_reviewing = conversation.phase in (Phase.REVIEW, Phase.COMPLETE)
        phase = Phase.COMPLETE if (is_clean_confirm and was_reviewing) else Phase.REVIEW
    else:
        phase = _REASON_TO_PHASE[decision.reason]

    return TurnResult(ConversationState(booking, phase), decision)
