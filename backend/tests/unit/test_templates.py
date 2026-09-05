"""Every sentence the agent speaks -- MASTER_PLAN.md step 2.4.

Most tests here drive real state through the actual reducer and policy
(the same pipeline production code uses) rather than hand-constructing
Field objects directly -- that is what caught the real bug this module's
first manual run found (an enum stored as a raw string by the reducer,
surfaced only once templates.py tried to read it back as an enum).
"""

from datetime import datetime

import pytest

from app.conversation.templates import (
    compose_acknowledgment,
    compose_question,
    compose_turn_response,
    format_date_full,
    format_date_short,
    format_floor,
    format_item,
    format_item_list,
    format_vehicle,
)
from app.domain import completeness, policy
from app.domain.reducer import apply, confirm_all
from app.domain.specs import get_field
from app.domain.state import BookingState, FieldStatus, Patch, PatchOp

REF = datetime(2026, 9, 11, 10, 0)  # Friday


def _apply(state, *patches):
    return apply(state, list(patches), reference=REF).state


# --- formatters --------------------------------------------------------


def test_format_floor():
    assert format_floor(0) == "the ground floor"
    assert format_floor(1) == "the 1st floor"
    assert format_floor(2) == "the 2nd floor"
    assert format_floor(3) == "the 3rd floor"
    assert format_floor(4) == "the 4th floor"
    assert format_floor(11) == "the 11th floor"  # not "11st"
    assert format_floor(21) == "the 21st floor"


def test_format_date_short_and_full():
    assert format_date_short("2026-09-12") == "Saturday"
    assert format_date_full("2026-09-12") == "Saturday, 12 September"


def test_format_vehicle_covers_every_enum_value():
    from app.domain.state import VehicleType

    for vehicle in VehicleType:
        text = format_vehicle(vehicle)
        assert text and isinstance(text, str)


def test_format_item_pluralises_correctly():
    from app.domain.state import Item

    assert format_item(Item(name="sofa", quantity=1)) == "a sofa"
    assert format_item(Item(name="cupboard", quantity=2)) == "2 cupboards"


def test_format_item_list_joins_naturally():
    from app.domain.state import Item

    assert format_item_list([Item(name="sofa", quantity=1)]) == "a sofa"
    assert (
        format_item_list([Item(name="sofa", quantity=1), Item(name="cupboard", quantity=2)])
        == "a sofa and 2 cupboards"
    )


# --- acknowledgment: what counts as "landed" ----------------------------


def test_no_acknowledgment_when_nothing_landed():
    state = BookingState()
    assert compose_acknowledgment([], state) is None


def test_a_conflict_rejected_patch_is_not_acknowledged():
    """The field the reducer rejected must not be acknowledged as accepted
    -- it is left for the conflict question instead."""
    state = _apply(
        BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")
    )
    state = confirm_all(state)
    attempted = Patch(op=PatchOp.SET, field="pickup.locality", value="Whitefield")
    state = _apply(state, attempted)  # rejected: conflict recorded, value unchanged

    assert compose_acknowledgment([attempted], state) is None


def test_a_correction_gets_the_updated_lead_in():
    state = _apply(
        BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")
    )
    correction = Patch(op=PatchOp.CORRECT, field="pickup.locality", value="Whitefield")
    state = _apply(state, correction)
    ack = compose_acknowledgment([correction], state)
    assert ack.startswith("Got it, updated")


def test_fresh_info_gets_the_plain_lead_in():
    patch = Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")
    state = _apply(BookingState(), patch)
    ack = compose_acknowledgment([patch], state)
    assert ack.startswith("Got it —")
    assert "updated" not in ack


# --- acknowledgment: content -----------------------------------------------


def test_both_locations_combine_as_x_to_y():
    patches = [
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
    ]
    state = _apply(BookingState(), *patches)
    ack = compose_acknowledgment(patches, state)
    assert "Koramangala to Whitefield" in ack


def test_a_single_location_does_not_claim_the_other_one_changed():
    patch = Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")
    state = _apply(BookingState(), patch)
    ack = compose_acknowledgment([patch], state)
    assert "Koramangala" in ack
    assert " to " not in ack  # not phrased as if drop were also just given


def test_category_is_suppressed_when_items_are_also_acknowledged():
    """Naming the actual items already conveys what is being moved --
    restating the category too is redundant, not something anyone would say."""
    patches = [
        Patch(op=PatchOp.SET, field="goods.category", value="furniture"),
        Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa", "quantity": 1}),
    ]
    state = _apply(BookingState(), *patches)
    ack = compose_acknowledgment(patches, state)
    assert "sofa" in ack
    assert "furniture" not in ack


def test_category_is_acknowledged_on_its_own():
    patch = Patch(op=PatchOp.SET, field="goods.category", value="furniture")
    state = _apply(BookingState(), patch)
    ack = compose_acknowledgment([patch], state)
    assert "furniture" in ack


@pytest.mark.parametrize(
    ("field", "value", "expect_in", "expect_not_in"),
    [
        ("service.needs_disassembly", False, "no disassembly needed", "disassembly needed"),
        ("service.needs_disassembly", True, "disassembly needed", "no disassembly"),
        ("service.needs_packing", False, "no packing help needed", "packing help needed"),
        ("service.needs_packing", True, "packing help", "no packing"),
    ],
)
def test_boolean_fields_are_acknowledged_either_way(field, value, expect_in, expect_not_in):
    """Regression: these two fields used to return None (no acknowledgment
    at all) for a False answer, silently dropping the user's "no" from the
    response -- found by reading real generated output, not a narrower test."""
    patch = Patch(op=PatchOp.SET, field=field, value=value)
    state = _apply(BookingState(), patch)
    ack = compose_acknowledgment([patch], state)
    assert ack is not None
    assert expect_in in ack


def test_schedule_combines_date_and_time():
    # needs_normalization=True means a raw phrase, not an already-resolved
    # ISO date -- REF is Friday 11 Sep, so "Saturday" resolves to the 12th.
    patches = [
        Patch(op=PatchOp.SET, field="schedule.date", value="Saturday", needs_normalization=True),
        Patch(
            op=PatchOp.SET, field="schedule.time_window", value="evening", needs_normalization=True
        ),
    ]
    state = _apply(BookingState(), *patches)
    ack = compose_acknowledgment(patches, state)
    assert "Saturday" in ack and "evening" in ack


def test_is_asap_overrides_date_and_time_phrasing():
    patch = Patch(op=PatchOp.SET, field="schedule.is_asap", value=True)
    state = _apply(BookingState(), patch)
    ack = compose_acknowledgment([patch], state)
    assert "as soon as possible" in ack


# --- questions ------------------------------------------------------------


def test_missing_question_for_every_required_and_conditional_field_is_non_empty():
    """Every field the policy can ever select for reason=MISSING must have
    real question text -- including the fallback path for one that has no
    hand-written variants, which would otherwise surface as a raw KeyError
    mid-conversation instead of a graceful (if generic) question."""
    from app.domain.policy import SlotDecision, SlotReason
    from app.domain.specs import FIELD_SPECS

    state = BookingState()
    for spec in FIELD_SPECS:
        decision = SlotDecision(spec.field_path, spec.label, SlotReason.MISSING)
        question = compose_question(decision, state)
        assert question and isinstance(question, str)


def test_floor_question_mentions_the_locality_when_known():
    state = _apply(
        BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")
    )
    new_state, decision = policy.sweep_and_select(
        _apply(state, Patch(op=PatchOp.SET, field="goods.category", value="furniture"))
    )
    # Force the decision to pickup.floor directly, regardless of what the
    # policy would pick next, since this test is about the question's
    # wording for that specific slot, not the priority ordering.
    from app.domain.policy import SlotDecision, SlotReason

    decision = SlotDecision("pickup.floor", "pickup floor", SlotReason.MISSING)
    question = compose_question(decision, new_state)
    assert "Koramangala" in question


def test_floor_question_is_generic_without_a_known_locality():
    from app.domain.policy import SlotDecision, SlotReason

    decision = SlotDecision("pickup.floor", "pickup floor", SlotReason.MISSING)
    question = compose_question(decision, BookingState())
    assert "floor" in question.lower()


def test_question_variants_rotate_across_turns():
    from app.domain.policy import SlotDecision, SlotReason

    decision = SlotDecision("pickup.locality", "pickup location", SlotReason.MISSING)
    seen = set()
    for turn in range(6):
        state = BookingState().model_copy(update={"turn": turn})
        seen.add(compose_question(decision, state))
    assert len(seen) > 1, "the same question was produced for every turn -- no rotation happened"


def test_ambiguity_question_is_keyed_by_reason():
    state = _apply(BookingState(), Patch(op=PatchOp.SET, field="drop.locality", value="Kochi"))
    assert get_field(state, "drop.locality").status == FieldStatus.AMBIGUOUS

    new_state, decision = policy.sweep_and_select(state)
    assert decision.reason.value == "ambiguous"
    question = compose_question(decision, new_state)
    assert "Kochi" in question


def test_conflict_question_mentions_both_values():
    state = _apply(
        BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")
    )
    state = confirm_all(state)
    state = _apply(state, Patch(op=PatchOp.SET, field="pickup.locality", value="Whitefield"))

    new_state, decision = policy.sweep_and_select(state)
    assert decision.reason.value == "conflict"
    question = compose_question(decision, new_state)
    assert "Koramangala" in question and "Whitefield" in question


def test_confirm_inferred_question_names_the_actual_guess():
    """Fills every REQUIRED and CONDITIONAL field directly (a fixed two
    passes: floor/lift only become required once category is known) rather
    than looping until the policy reaches confirm_inferred -- a loop driven
    by a hand-typed filler list is exactly the kind of thing that hangs
    forever the moment one field is forgotten, which is what the first
    version of this test actually did."""
    state = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
        Patch(op=PatchOp.SET, field="goods.category", value="furniture"),
        Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"}),
        # needs_normalization=True means a raw phrase, not an already-resolved
        # ISO date -- REF is Friday 11 Sep, so "Saturday" resolves to the 12th.
        Patch(op=PatchOp.SET, field="schedule.date", value="Saturday", needs_normalization=True),
        Patch(
            op=PatchOp.SET, field="schedule.time_window", value="evening", needs_normalization=True
        ),
    )
    # category=furniture makes floor/lift conditionally required; fill them
    # in a second pass now that they are known to apply.
    state = _apply(
        state,
        Patch(op=PatchOp.SET, field="pickup.floor", value=0),
        Patch(op=PatchOp.SET, field="drop.floor", value=0),
        Patch(op=PatchOp.SET, field="service.needs_disassembly", value=False),
        Patch(op=PatchOp.SET, field="service.needs_packing", value=False),
    )

    new_state, decision = policy.sweep_and_select(state)
    assert decision is not None and decision.reason.value == "confirm_inferred", (
        f"expected to reach confirm_inferred, got {decision!r} -- "
        f"missing: {completeness.missing(new_state)}"
    )
    question = compose_question(decision, new_state)
    assert "Tata Ace" in question  # sofa alone -> Tata Ace, per inference.py's calibration


# --- putting a turn together ------------------------------------------


def test_compose_turn_response_joins_acknowledgment_and_question():
    from app.domain.policy import SlotDecision, SlotReason

    patch = Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")
    state = _apply(BookingState(), patch)
    decision = SlotDecision("drop.locality", "drop location", SlotReason.MISSING)
    response = compose_turn_response([patch], state, decision)
    assert "Koramangala" in response
    assert response.strip().endswith("?")


def test_compose_turn_response_with_nothing_to_acknowledge_is_just_the_question():
    from app.domain.policy import SlotDecision, SlotReason

    decision = SlotDecision("pickup.locality", "pickup location", SlotReason.MISSING)
    response = compose_turn_response([], BookingState(), decision)
    assert response == compose_question(decision, BookingState())
