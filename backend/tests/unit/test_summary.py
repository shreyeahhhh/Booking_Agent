"""The final booking summary -- MASTER_PLAN.md step 2.6."""

from datetime import datetime

from app.conversation.summary import (
    _format_exact_time,
    _format_hour_range,
    build_summary,
    render_summary,
)
from app.domain.reducer import apply, confirm_all
from app.domain.state import BookingState, Patch, PatchOp, TimeWindow

REF = datetime(2026, 9, 11, 10, 0)  # Friday


def _apply(state, *patches):
    return apply(state, list(patches), reference=REF).state


def _labels(state):
    return [line.label for line in build_summary(state)]


# --- structure: sections appear only when populated ------------------------


def test_empty_state_has_a_placeholder_message():
    assert render_summary(BookingState()) == "Nothing has been recorded yet."


def test_sections_appear_only_when_populated():
    state = _apply(
        BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")
    )
    assert _labels(state) == ["Pickup"]


def test_full_booking_has_every_expected_section_in_order():
    state = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
        Patch(op=PatchOp.SET, field="goods.category", value="furniture"),
        Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"}),
        Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"),
        Patch(op=PatchOp.SET, field="schedule.time_window", value="evening"),
        Patch(op=PatchOp.SET, field="pickup.floor", value=3),
        Patch(op=PatchOp.SET, field="pickup.has_lift", value=True),
        Patch(op=PatchOp.SET, field="drop.floor", value=0),
        Patch(op=PatchOp.SET, field="service.needs_disassembly", value=False),
        Patch(op=PatchOp.SET, field="service.needs_packing", value=True),
        Patch(op=PatchOp.APPEND, field="notes", value="fragile"),
    )
    assert _labels(state) == ["Pickup", "Drop", "Date/time", "Items", "Vehicle", "Helpers", "Notes"]


# --- addresses --------------------------------------------------------------


def test_pickup_line_combines_locality_floor_and_lift():
    state = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="pickup.floor", value=3),
        Patch(op=PatchOp.SET, field="pickup.has_lift", value=True),
    )
    line = build_summary(state)[0]
    assert line.value == "Koramangala, 3rd floor, lift available"


def test_ground_floor_and_no_lift_are_worded_plainly():
    state = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="pickup.floor", value=0),
        Patch(op=PatchOp.SET, field="pickup.has_lift", value=False),
    )
    line = build_summary(state)[0]
    assert line.value == "Koramangala, ground floor, no lift"


def test_landmark_is_included_when_given():
    state = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="pickup.landmark", value="near the water tank"),
    )
    assert "near the water tank" in build_summary(state)[0].value


# --- schedule ----------------------------------------------------------------


def test_is_asap_overrides_date_and_time():
    state = _apply(BookingState(), Patch(op=PatchOp.SET, field="schedule.is_asap", value=True))
    line = next(line_ for line_ in build_summary(state) if line_.label == "Date/time")
    assert line.value == "as soon as possible"


def test_exact_time_is_shown_in_preference_to_the_window():
    state = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"),
        Patch(op=PatchOp.SET, field="schedule.time_window", value="evening"),
        Patch(op=PatchOp.SET, field="schedule.exact_time", value="16:30"),
    )
    line = next(line_ for line_ in build_summary(state) if line_.label == "Date/time")
    assert "4:30pm" in line.value
    assert "evening" not in line.value


def test_window_falls_back_to_the_hour_range_without_an_exact_time():
    state = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"),
        Patch(op=PatchOp.SET, field="schedule.time_window", value="evening"),
    )
    line = next(line_ for line_ in build_summary(state) if line_.label == "Date/time")
    assert "evening (4-8pm)" in line.value


def test_hour_range_for_every_window():
    assert _format_hour_range(TimeWindow.MORNING) == "6am-12pm"
    assert _format_hour_range(TimeWindow.AFTERNOON) == "12-4pm"
    assert _format_hour_range(TimeWindow.EVENING) == "4-8pm"
    assert _format_hour_range(TimeWindow.NIGHT) == "8pm-12am"


def test_exact_time_formatting_handles_midnight_and_noon():
    assert _format_exact_time("00:00") == "12:00am"
    assert _format_exact_time("12:00") == "12:00pm"
    assert _format_exact_time("09:05") == "9:05am"
    assert _format_exact_time("23:45") == "11:45pm"


# --- vehicle, helpers, notes -------------------------------------------------


def test_vehicle_line_has_no_leading_article():
    """format_vehicle returns "a Tata Ace" for prose; the table value must
    not repeat the article the way "Pickup  a Koramangala" never would."""
    state = _apply(
        BookingState(), Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa"})
    )
    line = next(line_ for line_ in build_summary(state) if line_.label == "Vehicle")
    assert line.value == "Tata Ace"
    assert not line.value.startswith(("a ", "an "))


def test_packing_and_disassembly_appear_only_when_true():
    state = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="service.needs_packing", value=True),
        Patch(op=PatchOp.SET, field="service.needs_disassembly", value=False),
    )
    line = next(line_ for line_ in build_summary(state) if line_.label == "Notes")
    assert "packing help" in line.value
    assert "disassembly" not in line.value


def test_free_text_notes_are_included():
    state = _apply(BookingState(), Patch(op=PatchOp.APPEND, field="notes", value="fragile"))
    line = next(line_ for line_ in build_summary(state) if line_.label == "Notes")
    assert line.value == "fragile"


# --- corrections and assumptions --------------------------------------------


def test_only_corrected_fields_appear_in_the_corrections_line():
    state = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
    )
    state = _apply(state, Patch(op=PatchOp.CORRECT, field="drop.locality", value="Sarjapur"))
    line = next(line_ for line_ in build_summary(state) if line_.label == "Corrected")
    assert "drop location" in line.value
    assert "pickup" not in line.value.lower()


def test_multiple_corrections_to_the_same_field_all_show():
    state = _apply(BookingState(), Patch(op=PatchOp.SET, field="schedule.date", value="2026-09-12"))
    state = _apply(state, Patch(op=PatchOp.CORRECT, field="schedule.date", value="2026-09-13"))
    state = _apply(state, Patch(op=PatchOp.CORRECT, field="schedule.date", value="2026-09-19"))
    line = next(line_ for line_ in build_summary(state) if line_.label == "Corrected")
    assert "2026-09-12" in line.value and "2026-09-13" in line.value


def test_a_field_with_no_spec_still_gets_a_readable_label_if_corrected():
    """booking_type has no FieldSpec (see specs.py's OPTIONAL_SCALAR_PATHS) --
    the corrections renderer must fall back to something readable rather
    than raise on spec_for's KeyError."""
    state = _apply(BookingState(), Patch(op=PatchOp.SET, field="booking_type", value="single_item"))
    state = _apply(state, Patch(op=PatchOp.CORRECT, field="booking_type", value="house_shifting"))
    line = next(line_ for line_ in build_summary(state) if line_.label == "Corrected")
    assert "booking type" in line.value.lower()


def test_no_corrections_line_when_nothing_was_corrected():
    state = _apply(
        BookingState(), Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala")
    )
    assert "Corrected" not in _labels(state)


def test_assumptions_are_shown():
    state = _apply(BookingState(), Patch(op=PatchOp.SET, field="drop.locality", value="Kochi"))
    from app.domain import policy

    state = policy.record_question_asked(state, "drop.locality")
    state = policy.record_question_asked(state, "drop.locality")
    state, _ = policy.sweep_and_select(state)
    line = next(line_ for line_ in build_summary(state) if line_.label == "Assumed")
    assert "drop location" in line.value


# --- end to end --------------------------------------------------------------


def test_full_conversation_renders_a_complete_readable_summary():
    """The project's own canonical example, confirmed and rendered -- the
    thing the PDF brief actually asks a reviewer to look at."""
    state = _apply(
        BookingState(),
        Patch(op=PatchOp.SET, field="pickup.locality", value="Koramangala"),
        Patch(op=PatchOp.SET, field="drop.locality", value="Whitefield"),
        Patch(op=PatchOp.SET, field="goods.category", value="furniture"),
        Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "sofa", "quantity": 1}),
        Patch(op=PatchOp.APPEND, field="goods.items", value={"name": "cupboard", "quantity": 2}),
        Patch(op=PatchOp.SET, field="schedule.date", value="Saturday", needs_normalization=True),
        Patch(
            op=PatchOp.SET, field="schedule.time_window", value="evening", needs_normalization=True
        ),
        Patch(op=PatchOp.SET, field="pickup.floor", value=3),
        Patch(op=PatchOp.SET, field="pickup.has_lift", value=True),
        Patch(op=PatchOp.SET, field="drop.floor", value=0),
        Patch(op=PatchOp.SET, field="service.needs_disassembly", value=False),
        Patch(op=PatchOp.SET, field="service.needs_packing", value=False),
    )
    state = confirm_all(state)
    text = render_summary(state)

    for expected in (
        "Koramangala",
        "3rd floor",
        "lift available",
        "Whitefield",
        "ground floor",
        "Saturday, 12 September",
        "evening (4-8pm)",
        "a sofa and 2 cupboards",
        "Tata Ace",
        "Helpers",
    ):
        assert expected in text, f"{expected!r} missing from:\n{text}"
