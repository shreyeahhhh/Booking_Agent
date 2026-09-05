"""Next-slot selection and bounded clarification -- MASTER_PLAN.md step 1.7."""

from datetime import datetime

from app.domain import policy
from app.domain.reducer import apply
from app.domain.specs import get_field
from app.domain.state import BookingState, FieldStatus, Patch, PatchOp

REF = datetime(2026, 9, 11, 10, 0)


def _apply(state, *patches):
    return apply(state, list(patches), reference=REF).state


def test_empty_state_asks_for_pickup_first():
    _, decision = policy.sweep_and_select(BookingState())
    assert decision.field_path == "pickup.locality"
    assert decision.reason == policy.SlotReason.MISSING


def test_a_filled_slot_is_never_selected_again():
    """The central "no repeat questions" guarantee: reading state alone
    already makes an answered slot unselectable."""
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"))
    _, decision = policy.sweep_and_select(s)
    assert decision.field_path != "pickup.locality"


def test_priority_order_is_respected():
    s = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
    )
    _, decision = policy.sweep_and_select(s)
    assert decision.field_path == "goods.category"  # priority 30, next unfilled


def test_conflicts_take_priority_over_everything_else():
    from app.domain.reducer import confirm_all

    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"))
    s = confirm_all(s)
    s = _apply(s, Patch(op=PatchOp.SET, field="pickup.locality", value="Whitefield"))  # -> conflict

    _, decision = policy.sweep_and_select(s)
    assert decision.field_path == "pickup.locality"
    assert decision.reason == policy.SlotReason.CONFLICT


def test_ambiguous_field_is_selected_before_plain_missing_lower_priority_ones():
    s = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="drop.locality", value="Kochi"),  # ambiguous, priority 20
    )
    _, decision = policy.sweep_and_select(s)
    assert decision.field_path == "drop.locality"
    assert decision.reason == policy.SlotReason.AMBIGUOUS


def test_confirm_inferred_is_only_reached_once_nothing_else_is_missing():
    s = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
        Patch(op=PatchOp.SET, field="goods.category", value="furniture"),
        Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "chair"}),
        Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"),
        Patch(op=PatchOp.SET, field="schedule.time_window", value="evening"),
        Patch(op=PatchOp.SET, field="pickup.floor", value=0),
        Patch(op=PatchOp.SET, field="drop.floor", value=0),
    )
    _, decision = policy.sweep_and_select(s)
    assert decision.reason == policy.SlotReason.CONFIRM_INFERRED


def test_nothing_left_returns_none():
    from app.domain.reducer import confirm_all

    s = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
        Patch(op=PatchOp.SET, field="goods.category", value="furniture"),
        Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "chair"}),
        Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"),
        Patch(op=PatchOp.SET, field="schedule.time_window", value="evening"),
        Patch(op=PatchOp.SET, field="pickup.floor", value=0),
        Patch(op=PatchOp.SET, field="drop.floor", value=0),
    )
    s = confirm_all(s)
    _, decision = policy.sweep_and_select(s)
    assert decision is None


def test_bounded_clarification_gives_up_after_max_attempts_and_records_an_assumption():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="drop.locality", value="Kochi"))
    assert get_field(s, "drop.locality").status == FieldStatus.AMBIGUOUS

    # Simulate the field being asked about twice and still coming back vague.
    s = policy.record_question_asked(s, "drop.locality")
    s = policy.record_question_asked(s, "drop.locality")
    assert get_field(s, "drop.locality").clarify_attempts == 2

    new_state, decision = policy.sweep_and_select(s, max_clarify_attempts=2)

    f = get_field(new_state, "drop.locality")
    assert f.status == FieldStatus.PROVIDED  # accepted as-is, not stuck ambiguous forever
    assert f.value == "Kochi"
    assert len(new_state.assumptions) == 1
    assert new_state.assumptions[0].field == "drop.locality"
    assert decision.field_path != "drop.locality"  # no longer re-selected


def test_record_question_asked_is_a_no_op_for_non_ambiguous_fields():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"))
    s2 = policy.record_question_asked(s, "pickup.locality")
    assert get_field(s2, "pickup.locality").clarify_attempts == 0


def test_below_the_attempt_cap_the_ambiguous_field_keeps_being_selected():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="drop.locality", value="Kochi"))
    s = policy.record_question_asked(s, "drop.locality")  # only 1 attempt so far

    _, decision = policy.sweep_and_select(s, max_clarify_attempts=2)

    assert decision.field_path == "drop.locality"
    assert decision.reason == policy.SlotReason.AMBIGUOUS
