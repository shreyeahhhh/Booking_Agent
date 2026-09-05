"""Patch application, corrections, conflicts and cascades -- MASTER_PLAN.md step 1.5."""

from datetime import datetime

from app.domain.reducer import apply, confirm_all
from app.domain.specs import get_field
from app.domain.state import BookingState, FieldStatus, Patch, PatchOp

REF = datetime(2026, 9, 11, 10, 0)


def _apply(state, *patches):
    return apply(state, list(patches), reference=REF).state


def test_set_on_empty_field_provides_it():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"))
    f = get_field(s, "pickup.locality")
    assert f.value == "Koramangala"
    assert f.status == FieldStatus.PROVIDED


def test_an_enum_typed_field_is_stored_as_a_real_enum_member_not_a_raw_string():
    """Regression: the reducer used to build the new Field via bare
    Field(...), which has no bound type parameter for Pydantic to coerce
    against, so "furniture" was stored as a plain str. Harmless everywhere a
    StrEnum's string-equality masked it (specs.py's predicates, dict lookups
    by string key), but wrong, and it broke the first templates.py code that
    called .value expecting an actual enum instance. Caught by driving a
    real conversation through templates.py, not by any narrower unit test."""
    from app.domain.state import GoodsCategory

    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="goods.category", value="furniture"))
    value = get_field(s, "goods.category").value
    assert value is GoodsCategory.FURNITURE
    assert isinstance(value, GoodsCategory)


def test_turn_counter_increments_once_per_apply_call():
    s = BookingState()
    s = _apply(s, Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"))
    assert s.turn == 1
    s = _apply(s, Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"))
    assert s.turn == 2


def test_set_against_a_confirmed_field_is_rejected_as_a_conflict():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"))
    s = confirm_all(s)

    s2 = _apply(s, Patch(op=PatchOp.SET, field="pickup.locality", value="Whitefield"))

    assert get_field(s2, "pickup.locality").value == "Koramangala"  # unchanged
    assert len(s2.conflicts) == 1
    assert s2.conflicts[0].field == "pickup.locality"
    assert s2.conflicts[0].existing_value == "Koramangala"
    assert s2.conflicts[0].attempted_value == "Whitefield"


def test_correct_overrides_a_confirmed_field_and_resolves_the_conflict():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"))
    s = confirm_all(s)
    s = _apply(s, Patch(op=PatchOp.SET, field="pickup.locality", value="Whitefield"))  # -> conflict
    assert len(s.conflicts) == 1

    s2 = _apply(
        s,
        Patch(
            op=PatchOp.CORRECT,
            field="pickup.locality",
            value="Whitefield",
            previous_value="Koramangala",
        ),
    )

    assert get_field(s2, "pickup.locality").value == "Whitefield"
    assert s2.conflicts == []


def test_correct_pushes_the_old_value_into_revisions():
    s = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12", evidence="tomorrow"),
    )
    s2 = _apply(
        s, Patch(op=PatchOp.CORRECT, field="schedule.date", value="2026-09-13", evidence="Sunday")
    )

    f = get_field(s2, "schedule.date")
    assert f.value == "2026-09-13"
    assert len(f.revisions) == 1
    assert f.revisions[0].value == "2026-09-12"


def test_multiple_corrections_accumulate_full_history():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"))
    s = _apply(s, Patch(op=PatchOp.CORRECT, field="schedule.date", value="2026-09-13"))
    s = _apply(s, Patch(op=PatchOp.CORRECT, field="schedule.date", value="2026-09-19"))

    f = get_field(s, "schedule.date")
    assert f.value == "2026-09-19"
    assert [r.value for r in f.revisions] == ["2026-09-12", "2026-09-13"]


def test_clear_resets_a_field_to_empty():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"))
    s = _apply(s, Patch(op=PatchOp.CLEAR, field="pickup.locality"))
    f = get_field(s, "pickup.locality")
    assert f.status == FieldStatus.EMPTY
    assert f.value is None


def test_out_of_order_patches_all_land_correctly():
    """The same five facts, applied in scrambled order, must produce the same
    state as applying them in the "natural" order would."""
    s = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.floor", value=3),
        Patch(op=PatchOp.SET, field="drop.locality", value="Edappally"),
        Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-19"),
        Patch(op=PatchOp.SET, field="pickup.has_lift", value=False),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Kakkanad"),
    )
    assert get_field(s, "pickup.locality").value == "Kakkanad"
    assert get_field(s, "drop.locality").value == "Edappally"
    assert get_field(s, "schedule.date").value == "2026-09-19"
    assert get_field(s, "pickup.floor").value == 3
    assert get_field(s, "pickup.has_lift").value is False


def test_date_normalization_failure_marks_the_field_ambiguous_not_empty():
    # September (REF's month) genuinely has 30 days -- use a February
    # reference so "the 30th" is actually unresolvable, per
    # docs/test-plan.md's date table.
    february = datetime(2026, 2, 10, 10, 0)
    result = apply(
        BookingState(),
        [Patch(op=PatchOp.SET, field="schedule.date", value="the 30th", needs_normalization=True)],
        reference=february,
    )
    f = get_field(result.state, "schedule.date")
    assert f.status == FieldStatus.AMBIGUOUS
    assert f.ambiguity is not None


def test_time_patch_fills_both_window_and_exact_time():
    s = _apply(
        BookingState(),
        Patch(
            op=PatchOp.SET,
            field="schedule.time_window",
            value="half past four",
            needs_normalization=True,
        ),
    )
    assert get_field(s, "schedule.exact_time").value == "16:30"
    from app.domain.state import TimeWindow

    assert get_field(s, "schedule.time_window").value == TimeWindow.EVENING


def test_bare_city_is_flagged_ambiguous_even_without_llm_flagging_it():
    """The reducer's own validator, independent of what the patch claims."""
    s = _apply(
        BookingState(), Patch(op=PatchOp.SET, field="drop.locality", value="Kochi", confidence=0.95)
    )
    f = get_field(s, "drop.locality")
    assert f.status == FieldStatus.AMBIGUOUS
    from app.domain.state import AmbiguityReason

    assert f.ambiguity == AmbiguityReason.CITY_LEVEL_ONLY


def test_a_specific_locality_is_not_flagged_ambiguous():
    s = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="drop.locality", value="Kakkanad", confidence=0.95),
    )
    assert get_field(s, "drop.locality").status == FieldStatus.PROVIDED


def test_correcting_a_bare_city_into_a_locality_clears_the_ambiguity():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="drop.locality", value="Kochi"))
    assert get_field(s, "drop.locality").status == FieldStatus.AMBIGUOUS

    s2 = _apply(
        s,
        Patch(op=PatchOp.CORRECT, field="drop.locality", value="Kakkanad", previous_value="Kochi"),
    )
    assert get_field(s2, "drop.locality").status == FieldStatus.PROVIDED
    assert get_field(s2, "drop.locality").value == "Kakkanad"


# --- Notes ("any additional requirements") ---------------------------------


def test_appending_a_note_records_it():
    s = _apply(
        BookingState(), Patch(op=PatchOp.APPEND, field="notes", value="fragile, handle with care")
    )
    assert len(s.notes) == 1
    assert s.notes[0].text == "fragile, handle with care"
    assert s.notes[0].turn == 1


def test_multiple_notes_accumulate():
    s = _apply(BookingState(), Patch(op=PatchOp.APPEND, field="notes", value="fragile"))
    s = _apply(s, Patch(op=PatchOp.APPEND, field="notes", value="call before arriving"))
    assert [n.text for n in s.notes] == ["fragile", "call before arriving"]


def test_empty_note_is_not_recorded():
    result = apply(
        BookingState(), [Patch(op=PatchOp.APPEND, field="notes", value="   ")], reference=REF
    )
    assert result.state.notes == []


def test_clear_removes_all_notes():
    s = _apply(BookingState(), Patch(op=PatchOp.APPEND, field="notes", value="fragile"))
    s = _apply(s, Patch(op=PatchOp.CLEAR, field="notes"))
    assert s.notes == []


# --- Items and cascade invalidation ----------------------------------------


def test_appending_items_infers_a_vehicle():
    s = _apply(
        BookingState(), Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"})
    )
    assert len(s.goods.items) == 1
    assert get_field(s, "service.vehicle_type").status == FieldStatus.INFERRED


def test_correcting_item_quantity_by_name_updates_it_in_place():
    s = _apply(
        BookingState(),
        Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "cupboard", "quantity": 2}),
    )
    s2 = _apply(
        s,
        Patch(
            op=PatchOp.CORRECT,
            field="goods.items",
            value={"name": "cupboard", "quantity": 3},
            previous_value={"name": "cupboard", "quantity": 2},
        ),
    )
    assert len(s2.goods.items) == 1
    assert s2.goods.items[0].quantity == 3


def test_correcting_an_unknown_item_name_falls_back_to_appending():
    s = _apply(
        BookingState(),
        Patch(op=PatchOp.CORRECT, field="goods.items", value={"name": "sofa", "quantity": 1}),
    )
    assert len(s.goods.items) == 1
    assert s.goods.items[0].name == "sofa"


def test_removing_an_item_recomputes_the_inferred_vehicle():
    s = _apply(
        BookingState(),
        Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"}),
        Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "fridge"}),
    )
    s2 = _apply(s, Patch(op=PatchOp.REMOVE, field="goods.items", value="fridge"))
    assert [i.name for i in s2.goods.items] == ["sofa"]


def test_clearing_items_resets_the_inferred_vehicle_to_empty():
    s = _apply(
        BookingState(), Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"})
    )
    assert get_field(s, "service.vehicle_type").status == FieldStatus.INFERRED

    s2 = _apply(s, Patch(op=PatchOp.CLEAR, field="goods.items"))
    assert s2.goods.items == []
    assert get_field(s2, "service.vehicle_type").status == FieldStatus.EMPTY


def test_item_change_never_overwrites_an_explicitly_confirmed_vehicle():
    """Adding a box after the user confirmed a vehicle must not silently
    replace their choice with a fresh system guess."""
    s = _apply(
        BookingState(), Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"})
    )
    s = confirm_all(s)
    assert get_field(s, "service.vehicle_type").status == FieldStatus.CONFIRMED
    confirmed_vehicle = get_field(s, "service.vehicle_type").value

    s2 = _apply(s, Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "box"}))

    f = get_field(s2, "service.vehicle_type")
    assert f.status == FieldStatus.CONFIRMED
    assert f.value == confirmed_vehicle


def test_confirm_all_only_promotes_provided_and_inferred_fields():
    s = _apply(BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"))
    s = confirm_all(s)
    assert get_field(s, "pickup.locality").status == FieldStatus.CONFIRMED
    assert (
        get_field(s, "drop.locality").status == FieldStatus.EMPTY
    )  # untouched, was never provided


def test_unsupported_op_on_a_scalar_field_is_a_safe_no_op():
    s = BookingState()
    result = apply(s, [Patch(op=PatchOp.APPEND, field="pickup.locality", value="x")], reference=REF)
    assert get_field(result.state, "pickup.locality").status == FieldStatus.EMPTY
    assert result.events  # logged, not silently swallowed


def test_unresolvable_field_path_does_not_crash_the_whole_turn():
    s = BookingState()
    result = apply(
        s,
        [
            Patch(op=PatchOp.SET, field="not.a.real.field", value="x"),
            Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        ],
        reference=REF,
    )
    assert get_field(result.state, "pickup.locality").value == "Koramangala"
