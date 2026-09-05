"""The conversation state machine -- MASTER_PLAN.md step 2.5."""

from datetime import datetime

from app.conversation.machine import ConversationState, Phase, advance, start
from app.domain.specs import get_field
from app.domain.state import ExtractionResult, FieldStatus, Intent, Patch, PatchOp

REF = datetime(2026, 9, 11, 10, 0)


def _turn(conversation, patches, intent=Intent.PROVIDE_INFO):
    extraction = ExtractionResult(intent=intent, patches=patches)
    return advance(conversation, extraction, reference=REF)


_PARCEL_BOOKING = [
    Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
    Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
    # goods.items is unconditionally required (see specs.py); a "documents"
    # item both satisfies that and gets goods.category auto-inferred as
    # parcel_documents (domain.inference.infer_category), with no floor/lift
    # predicates engaged the way a furniture item would trigger.
    Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "documents"}),
    Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"),
    Patch(op=PatchOp.SET, field="schedule.time_window", value="evening"),
]


def test_start_is_greeting_with_an_empty_booking():
    conversation = start()
    assert conversation.phase == Phase.GREETING
    assert conversation.booking.turn == 0


def test_first_turn_with_partial_info_moves_to_gathering():
    result = _turn(start(), [Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")])
    assert result.conversation.phase == Phase.GATHERING
    assert result.decision is not None


def _reviewing_parcel_booking() -> ConversationState:
    """Drives _PARCEL_BOOKING all the way to REVIEW.

    Not a one-turn shortcut: infer_vehicle_and_helpers proposes a vehicle for
    *any* non-empty item list (even a zero-load "documents" item -- a
    courier still needs some vehicle), so every complete booking passes
    through CONFIRM_INFERRED, parcels included. Confirming that guess is
    what this helper does before handing back a conversation actually at
    REVIEW, rather than each test re-deriving the same two-turn sequence.
    """
    at_confirm = _turn(start(), _PARCEL_BOOKING).conversation
    assert at_confirm.phase == Phase.CONFIRM_INFERRED, at_confirm.phase
    return _turn(at_confirm, [], intent=Intent.CONFIRM).conversation


def test_a_complete_parcel_booking_passes_through_confirm_inferred_then_reaches_review():
    at_confirm = _turn(start(), _PARCEL_BOOKING).conversation
    assert at_confirm.phase == Phase.CONFIRM_INFERRED
    reviewing = _turn(at_confirm, [], intent=Intent.CONFIRM).conversation
    assert reviewing.phase == Phase.REVIEW


def test_clean_confirmation_at_review_completes_the_booking():
    reviewing = _reviewing_parcel_booking()
    result = _turn(reviewing, [], intent=Intent.CONFIRM)
    assert result.conversation.phase == Phase.COMPLETE
    assert result.decision is None


def test_a_confirm_intent_with_attached_patches_is_not_a_clean_confirm():
    """docs/architecture.md's own example of a not-clean confirmation:
    "yes but change the time to 4pm". Must apply the correction and stay in
    REVIEW to re-present, never jump straight to COMPLETE on the same turn."""
    reviewing = _reviewing_parcel_booking()
    result = _turn(
        reviewing,
        [Patch(op=PatchOp.CORRECT, field="schedule.time_window", value="afternoon")],
        intent=Intent.CONFIRM,
    )
    assert result.conversation.phase == Phase.REVIEW
    assert get_field(result.conversation.booking, "schedule.time_window").value.value == "afternoon"


def test_a_simple_correction_during_review_stays_at_review():
    reviewing = _reviewing_parcel_booking()
    result = _turn(
        reviewing,
        [Patch(op=PatchOp.CORRECT, field="schedule.date", value="2026-09-13")],
        intent=Intent.CORRECTION,
    )
    assert result.conversation.phase == Phase.REVIEW
    assert result.decision is None


def test_a_scope_expanding_correction_during_review_reopens_gathering():
    """test-plan.md scenario 13: correcting the category to something that
    now requires floor/lift details must genuinely reopen gathering for
    them, not force a stay at REVIEW the way a simple field fix does. A
    hard-coded "corrections always return to review" rule would get this
    wrong. goods.items is already satisfied (the parcel's "documents"), so
    this specific correction's new requirement is pickup.floor -- furniture
    engages the floor/lift predicates a document never would."""
    reviewing = _reviewing_parcel_booking()
    result = _turn(
        reviewing,
        [Patch(op=PatchOp.CORRECT, field="goods.category", value="furniture")],
        intent=Intent.CORRECTION,
    )
    assert result.conversation.phase == Phase.GATHERING
    assert result.decision.field_path == "pickup.floor"


def test_furniture_booking_passes_through_confirm_inferred_before_review():
    conversation = start()
    result = _turn(
        conversation,
        [
            *_PARCEL_BOOKING[:2],  # locations
            Patch(op=PatchOp.SET, field="goods.category", value="furniture"),
            Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"}),
            *_PARCEL_BOOKING[3:],  # date, time
            Patch(op=PatchOp.SET, field="pickup.floor", value=0),
            Patch(op=PatchOp.SET, field="drop.floor", value=0),
            Patch(op=PatchOp.SET, field="service.needs_disassembly", value=False),
            Patch(op=PatchOp.SET, field="service.needs_packing", value=False),
        ],
    )
    assert result.conversation.phase == Phase.CONFIRM_INFERRED
    assert result.decision.field_path == "service.vehicle_type"


def test_confirming_inferred_vehicle_and_helpers_together_reaches_review():
    """One combined question (templates.py's _confirm_inferred_question
    phrases vehicle and helpers as a single "sound good?"), so one clean
    confirm must resolve both fields at once, not require asking twice."""
    conversation = start()
    at_confirm = _turn(
        conversation,
        [
            *_PARCEL_BOOKING[:2],
            Patch(op=PatchOp.SET, field="goods.category", value="furniture"),
            Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"}),
            *_PARCEL_BOOKING[3:],
            Patch(op=PatchOp.SET, field="pickup.floor", value=0),
            Patch(op=PatchOp.SET, field="drop.floor", value=0),
            Patch(op=PatchOp.SET, field="service.needs_disassembly", value=False),
            Patch(op=PatchOp.SET, field="service.needs_packing", value=False),
        ],
    ).conversation
    assert at_confirm.phase == Phase.CONFIRM_INFERRED

    result = _turn(at_confirm, [], intent=Intent.CONFIRM)
    assert result.conversation.phase == Phase.REVIEW
    assert result.decision is None


def test_a_conflict_routes_to_clarifying():
    from app.domain.reducer import confirm_all

    confirmed = confirm_all(_turn(start(), _PARCEL_BOOKING).conversation.booking)
    conversation = ConversationState(booking=confirmed, phase=Phase.REVIEW)

    result = _turn(
        conversation, [Patch(op=PatchOp.SET, field="pickup.locality", value="Whitefield")]
    )
    assert result.conversation.phase == Phase.CLARIFYING
    assert result.decision.reason.value == "conflict"


def test_ambiguity_routes_to_clarifying():
    result = _turn(start(), [Patch(op=PatchOp.SET, field="drop.locality", value="Kochi")])
    assert result.conversation.phase == Phase.CLARIFYING
    assert result.decision.reason.value == "ambiguous"


def test_bounded_clarification_eventually_gives_up_and_moves_on():
    """End-to-end proof that the give-up mechanism (built and unit-tested in
    isolation in phase 1) actually fires in a real conversation -- nothing
    called domain.policy.record_question_asked anywhere before this module
    wired it in, so this bound could never have been reached before."""
    conversation = start()
    r1 = _turn(conversation, [Patch(op=PatchOp.SET, field="drop.locality", value="Kochi")])
    assert r1.conversation.phase == Phase.CLARIFYING

    r2 = _turn(r1.conversation, [Patch(op=PatchOp.SET, field="drop.locality", value="Kochi")])
    assert r2.conversation.phase == Phase.CLARIFYING  # second attempt, still asking

    r3 = _turn(r2.conversation, [], intent=Intent.UNCLEAR)
    assert r3.conversation.phase != Phase.CLARIFYING  # gives up, moves on
    field = get_field(r3.conversation.booking, "drop.locality")
    assert field.status == FieldStatus.PROVIDED  # accepted despite the ambiguity
    assert len(r3.conversation.booking.assumptions) == 1
    assert r3.conversation.booking.assumptions[0].field == "drop.locality"


def test_off_topic_intent_with_no_patches_changes_nothing():
    conversation = _turn(
        start(), [Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")]
    ).conversation
    result = _turn(conversation, [], intent=Intent.OFF_TOPIC)
    assert result.conversation.booking == conversation.booking
    assert result.conversation.phase == conversation.phase
