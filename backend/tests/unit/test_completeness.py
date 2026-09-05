"""missing / ambiguous / conflicts / can_enter_review -- MASTER_PLAN.md step 1.6."""

from datetime import datetime

from app.domain import completeness
from app.domain.reducer import apply
from app.domain.state import BookingState, Patch, PatchOp

REF = datetime(2026, 9, 11, 10, 0)


def _apply(state, *patches):
    return apply(state, list(patches), reference=REF).state


def test_empty_state_is_missing_the_unconditional_required_fields():
    missing = completeness.missing(BookingState())
    for path in (
        "pickup.locality",
        "drop.locality",
        "goods.category",
        "schedule.date",
        "schedule.time_window",
    ):
        assert path in missing


def test_empty_state_cannot_enter_review():
    assert completeness.can_enter_review(BookingState()) is False


def test_ambiguous_field_counts_as_both_missing_and_ambiguous():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="drop.locality", value="Kochi"))
    assert "drop.locality" in completeness.missing(s)
    ambiguous = completeness.ambiguous(s)
    assert len(ambiguous) == 1
    assert ambiguous[0].field_path == "drop.locality"


def test_filling_every_required_field_clears_missing():
    s = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
        Patch(op=PatchOp.SET, field="goods.category", value="parcel_documents"),
        Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"),
        Patch(op=PatchOp.SET, field="schedule.time_window", value="evening"),
    )
    # booking_type left EMPTY -> goods.items still defaults to required (not a parcel yet)
    assert "goods.items" in completeness.missing(s)


def test_conflicts_are_read_directly_off_state():
    from app.domain.reducer import confirm_all

    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"))
    s = confirm_all(s)
    s = _apply(s, Patch(op=PatchOp.SET, field="pickup.locality", value="Whitefield"))

    conflicts = completeness.conflicts(s)
    assert len(conflicts) == 1
    assert completeness.can_enter_review(s) is False


def test_unconfirmed_inferred_only_appears_once_a_guess_exists():
    s = BookingState()
    assert completeness.unconfirmed_inferred(s) == []  # nothing to infer from yet

    s = _apply(s, Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"}))
    assert "service.vehicle_type" in completeness.unconfirmed_inferred(s)


def test_confirmed_inferred_field_is_no_longer_unconfirmed():
    from app.domain.reducer import confirm_all

    s = _apply(
        BookingState(), Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"})
    )
    s = confirm_all(s)
    assert "service.vehicle_type" not in completeness.unconfirmed_inferred(s)


def test_can_enter_review_ignores_unconfirmed_inferred_fields():
    """CONFIRM_INFERRED is a separate phase gate (phase 2) -- an unconfirmed
    guess must not block can_enter_review at this layer."""
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
    assert completeness.unconfirmed_inferred(s) != []
    assert completeness.can_enter_review(s) is True
