"""Fast-path output actually drives the real conversation machine --
MASTER_PLAN.md step 3.3.

test_fastpath.py proves classify() returns the right FastPathResult in
isolation; this file proves that result is genuinely consumable by
machine.advance() exactly the way an LLM-produced ExtractionResult is --
the same discipline every other phase 2/3 step has used (drive a realistic
multi-turn scenario through the real pipeline, not just unit-assert each
piece separately). This previews exactly how step 3.4's /turn endpoint will
compose the two modules: call classify() with the previous turn's decision,
and feed whatever it returns into advance() unchanged.
"""

from datetime import datetime

from app.conversation.fastpath import classify
from app.conversation.machine import Phase, advance, start
from app.domain.state import ExtractionResult, Intent, Patch, PatchOp

REF = datetime(2026, 9, 11, 10, 0)


def _turn(conversation, patches, intent=Intent.PROVIDE_INFO):
    extraction = ExtractionResult(intent=intent, patches=patches)
    return advance(conversation, extraction, reference=REF)


def _fastpath_turn(conversation, decision, utterance):
    """Exactly what step 3.4's endpoint will do: classify against the
    previous turn's pending decision, then advance with whatever comes
    back. Asserts along the way that the fast path actually fired, so a
    silently-declined match (falling through to None) fails loudly here
    instead of masquerading as a passing test of something else."""
    result = classify(utterance, phase=conversation.phase, decision=decision)
    assert result is not None, f"expected a fast-path match for {utterance!r}"
    assert result.extraction is not None, f"expected an extraction, not a meta-command: {utterance}"
    return advance(conversation, result.extraction, reference=REF)


def test_a_furniture_booking_completed_entirely_through_fast_path_answers():
    """Only the first turn (providing the actual booking facts) is a
    simulated extraction -- every yes/no and numeric answer after that is
    produced by the real fast-path classifier, not hand-constructed."""
    conversation = start()
    result = _turn(
        conversation,
        [
            Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
            Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
            Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa", "quantity": 1}),
            Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"),
            Patch(op=PatchOp.SET, field="schedule.time_window", value="evening"),
        ],
    )
    assert result.conversation.phase == Phase.GATHERING
    assert result.decision.field_path == "pickup.floor"

    # "3" answers the pending pickup.floor question via the numeric fast path.
    result = _fastpath_turn(result.conversation, result.decision, "3")
    assert get_value(result, "pickup.floor") == 3
    assert result.decision.field_path == "pickup.has_lift"  # floor >= 2 makes lift matter

    # "yes" answers the pending boolean lift question.
    result = _fastpath_turn(result.conversation, result.decision, "yes")
    assert get_value(result, "pickup.has_lift") is True
    assert result.decision.field_path == "drop.floor"

    result = _fastpath_turn(result.conversation, result.decision, "2")
    assert get_value(result, "drop.floor") == 2
    assert result.decision.field_path == "drop.has_lift"

    result = _fastpath_turn(result.conversation, result.decision, "no")
    assert get_value(result, "drop.has_lift") is False

    # Whatever is asked next (disassembly for a sofa, then the vehicle
    # guess) is answered the same way, via the real classifier, until
    # nothing is left to gather.
    while result.decision is not None and result.decision.field_path != "service.vehicle_type":
        result = _fastpath_turn(result.conversation, result.decision, "yes")

    assert result.conversation.phase == Phase.CONFIRM_INFERRED
    assert result.decision.field_path == "service.vehicle_type"

    # Confirm the inferred vehicle guess via fast path -> REVIEW.
    result = _fastpath_turn(result.conversation, result.decision, "yes")
    assert result.conversation.phase == Phase.REVIEW
    assert result.decision is None

    # Confirm the whole booking via fast path -> COMPLETE.
    result = _fastpath_turn(result.conversation, result.decision, "yes")
    assert result.conversation.phase == Phase.COMPLETE


def get_value(result, field_path):
    from app.domain.specs import get_field

    return get_field(result.conversation.booking, field_path).value
