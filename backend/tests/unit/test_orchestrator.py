"""conversation/orchestrator.py's own logic, tested directly rather than
only through the full _process_turn pipeline (test_process_turn.py) --
compose_response's was_already_complete branch is a text-composition
decision, not an STT/TTS concern, so it does not need either mocked to
verify precisely.
"""

from datetime import datetime

from app.conversation import templates
from app.conversation.machine import ConversationState, Phase, advance, start
from app.conversation.orchestrator import finish_turn
from app.domain.state import ExtractionResult, Intent, Patch, PatchOp

REF = datetime(2026, 9, 11, 10, 0)


def _turn(conversation: ConversationState, patches: list[Patch], intent=Intent.PROVIDE_INFO):
    extraction = ExtractionResult(intent=intent, patches=patches)
    return advance(conversation, extraction, reference=REF)


# Mirrors test_machine.py's identical fixture -- a minimal, genuinely
# complete booking, not an empty one: compose_response's was_already_complete
# branch only matters once sweep_and_select has nothing left to ask, which an
# empty booking (still missing every required field) never actually reaches.
_PARCEL_BOOKING = [
    Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
    Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
    Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "documents"}),
    Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"),
    Patch(op=PatchOp.SET, field="schedule.time_window", value="evening"),
]


def _completed_parcel_booking() -> ConversationState:
    """Drives _PARCEL_BOOKING all the way to COMPLETE (CONFIRM_INFERRED ->
    REVIEW -> COMPLETE, each a clean confirm) -- see test_machine.py's
    _reviewing_parcel_booking for why the extra CONFIRM_INFERRED hop is
    unavoidable for any non-empty item list."""
    at_confirm = _turn(start(), _PARCEL_BOOKING).conversation
    assert at_confirm.phase == Phase.CONFIRM_INFERRED, at_confirm.phase
    reviewing = _turn(at_confirm, [], intent=Intent.CONFIRM).conversation
    assert reviewing.phase == Phase.REVIEW, reviewing.phase
    completed = _turn(reviewing, [], intent=Intent.CONFIRM).conversation
    assert completed.phase == Phase.COMPLETE, completed.phase
    return completed


def test_reaching_completion_for_the_first_time_still_gets_the_full_summary():
    """was_already_complete must not swallow the *real* completion moment --
    only a courtesy remark said after the booking was already done skips
    the summary; reaching COMPLETE for the first time still needs the full
    read-back the user is meant to hear exactly once."""
    reviewing = _turn(start(), _PARCEL_BOOKING).conversation
    reviewing = _turn(reviewing, [], intent=Intent.CONFIRM).conversation  # -> REVIEW
    assert reviewing.phase == Phase.REVIEW

    extraction = ExtractionResult(intent=Intent.CONFIRM, patches=[])
    outcome = finish_turn(extraction, reviewing, reference=REF)

    assert outcome.conversation.phase == Phase.COMPLETE
    assert outcome.response_text != templates.POST_COMPLETION_ACKNOWLEDGMENT
    assert "Booking confirmed" in outcome.response_text
    assert "Koramangala" in outcome.response_text  # the actual summary, not a generic line


def test_a_courtesy_remark_after_completion_gets_a_short_reply_not_the_summary_again():
    """Live-confirmed gap: "thank you" once the booking was already COMPLETE
    used to fall through to the exact same rendering a brand-new completion
    gets -- re-composing (and, via TTS, re-speaking) the entire booking
    summary for a courtesy remark that changed nothing."""
    completed = _completed_parcel_booking()

    extraction = ExtractionResult(intent=Intent.CONFIRM, patches=[])
    outcome = finish_turn(extraction, completed, reference=REF)

    assert outcome.conversation.phase == Phase.COMPLETE
    assert outcome.response_text == templates.POST_COMPLETION_ACKNOWLEDGMENT
