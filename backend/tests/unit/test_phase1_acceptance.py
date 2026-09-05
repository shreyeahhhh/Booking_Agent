"""The MASTER_PLAN.md phase 1 acceptance test.

"A scripted test feeds patches in scrambled order, applies two corrections and
one contradiction, and asserts the exact final state -- with no network calls
and no API key set."

This is the one test that stands as proof the deterministic core works
end-to-end with zero AI involved: everything here is hand-constructed Patch
objects, exactly what an extractor (LLM or otherwise) would eventually
produce, but with no extractor in the loop at all.
"""

from datetime import datetime

from app.domain import completeness, policy
from app.domain.reducer import apply, confirm_all
from app.domain.specs import get_field
from app.domain.state import BookingState, FieldStatus, PatchOp
from app.domain.state import Patch as P

REF = datetime(2026, 9, 10, 10, 0)  # a real Thursday, so "tomorrow" and "Saturday"
# resolve to genuinely different dates below -- that's what makes the date
# correction later in this test a real, checkable change rather than two
# different phrasings that happen to land on the same day.


def test_full_scrambled_conversation_with_corrections_and_a_contradiction():
    state = BookingState()

    # --- Turn 1: scrambled order. Drop before pickup, date before category,
    # an item before either location. Mirrors test-plan.md scenario 2. ---
    state = apply(
        state,
        [
            P(
                op=PatchOp.SET,
                field="schedule.date",
                value="tomorrow",
                needs_normalization=True,
                evidence="tomorrow",
            ),
            P(op=PatchOp.APPEND, field="goods.items", value={"name": "cupboard", "quantity": 2}),
            P(op=PatchOp.SET, field="drop.locality", value="Whitefield", evidence="to Whitefield"),
            P(op=PatchOp.SET, field="goods.category", value="furniture"),
            P(
                op=PatchOp.SET,
                field="pickup.locality",
                value="Koramangala",
                evidence="from Koramangala",
            ),
        ],
        reference=REF,
    ).state

    assert get_field(state, "pickup.locality").value == "Koramangala"
    assert get_field(state, "drop.locality").value == "Whitefield"
    assert get_field(state, "schedule.date").value == "2026-09-11"  # "tomorrow" from Thursday
    assert state.goods.items[0].quantity == 2
    # Not yet complete: time window, floor (furniture engaged it) still missing.
    assert completeness.can_enter_review(state) is False

    # --- Turn 2: fill in what's still missing. A cupboard is on the bulky-
    # furniture keyword list (specs.py's `_needs_disassembly_check`), so that
    # question is engaged too, exactly as the conditional-requirement system
    # is supposed to do. ---
    state = apply(
        state,
        [
            P(op=PatchOp.SET, field="schedule.time_window", value="evening"),
            P(op=PatchOp.SET, field="pickup.floor", value=3),
            P(op=PatchOp.SET, field="pickup.has_lift", value=True),
            P(op=PatchOp.SET, field="drop.floor", value=0),
            P(op=PatchOp.SET, field="service.needs_disassembly", value=False),
        ],
        reference=REF,
    ).state

    # --- Correction 1: "Actually, not tomorrow. Saturday." A real change:
    # Thursday's "tomorrow" was Friday the 11th; "Saturday" is the 12th. ---
    state = apply(
        state,
        [
            P(
                op=PatchOp.CORRECT,
                field="schedule.date",
                value="Saturday",
                needs_normalization=True,
                previous_value="2026-09-11",
            )
        ],
        reference=REF,
    ).state
    date_field = get_field(state, "schedule.date")
    assert date_field.value == "2026-09-12"
    assert date_field.revisions[-1].value == "2026-09-11"  # the superseded "tomorrow" value

    # --- Correction 2: "It's three cupboards, not two." Must cascade into a
    # re-inferred vehicle, per design.md's worked example. ---
    state = apply(
        state,
        [
            P(
                op=PatchOp.CORRECT,
                field="goods.items",
                value={"name": "cupboard", "quantity": 3},
                previous_value={"name": "cupboard", "quantity": 2},
            )
        ],
        reference=REF,
    ).state
    assert state.goods.items[0].quantity == 3
    from app.domain.state import VehicleType

    vehicle = get_field(state, "service.vehicle_type")
    assert vehicle.status == FieldStatus.INFERRED
    assert vehicle.value == VehicleType.TATA_ACE

    # Confirm everything gathered so far, including the inferred vehicle --
    # this is what makes the upcoming contradiction meaningful: only a
    # CONFIRMED field can produce a conflict on a later `set`.
    state = confirm_all(state)
    assert completeness.can_enter_review(state) is True

    # --- Contradiction: the user now claims the pickup floor is the ground
    # floor, directly disputing the already-CONFIRMED 3rd floor. ---
    state = apply(state, [P(op=PatchOp.SET, field="pickup.floor", value=0)], reference=REF).state

    assert get_field(state, "pickup.floor").value == 3  # unchanged -- not silently overwritten
    assert len(state.conflicts) == 1
    assert state.conflicts[0].field == "pickup.floor"
    assert state.conflicts[0].existing_value == 3
    assert state.conflicts[0].attempted_value == 0
    assert completeness.can_enter_review(state) is False  # a pending conflict blocks review

    _, decision = policy.sweep_and_select(state)
    assert decision.field_path == "pickup.floor"
    assert decision.reason == policy.SlotReason.CONFLICT

    # The user clarifies: it really is the ground floor now (they moved pickup
    # to a different building, say) -- a `correct` resolves the contradiction.
    state = apply(
        state,
        [P(op=PatchOp.CORRECT, field="pickup.floor", value=0, previous_value=3)],
        reference=REF,
    ).state

    assert get_field(state, "pickup.floor").value == 0
    assert state.conflicts == []

    # --- Final state assertions -------------------------------------------
    assert get_field(state, "pickup.locality").value == "Koramangala"
    assert get_field(state, "drop.locality").value == "Whitefield"
    assert get_field(state, "schedule.date").value == "2026-09-12"  # corrected value survives
    assert get_field(state, "schedule.time_window").value == "evening"
    assert state.goods.items[0].name == "cupboard"
    assert state.goods.items[0].quantity == 3
    assert get_field(state, "pickup.floor").value == 0
    assert get_field(state, "drop.floor").value == 0
    assert get_field(state, "service.vehicle_type").value == VehicleType.TATA_ACE

    # Correcting pickup.floor from 3 to 0 means a lift is no longer relevant
    # there -- the conditional requirement re-evaluates freely, no cascade
    # logic needed, exactly as docs/design.md SS3.4 describes.
    from app.domain.specs import spec_for

    assert spec_for("pickup.has_lift").is_required_now(state) is False
