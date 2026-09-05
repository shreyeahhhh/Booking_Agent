"""FieldSpec predicates and dotted-path access -- MASTER_PLAN.md step 1.2."""

import pytest

from app.domain.specs import FIELD_SPECS, get_field, spec_for, with_field
from app.domain.state import BookingState, Field, FieldStatus, GoodsCategory


def test_every_priority_is_unique():
    priorities = [spec.priority for spec in FIELD_SPECS]
    assert len(priorities) == len(set(priorities))


def test_unconditional_required_fields_are_always_required():
    for path in (
        "pickup.locality",
        "drop.locality",
        "goods.category",
        "schedule.date",
        "schedule.time_window",
    ):
        assert spec_for(path).is_required_now(BookingState()) is True


def test_items_required_unless_parcel():
    from app.domain.state import BookingType

    state = BookingState()
    assert spec_for("goods.items").is_required_now(state) is True

    parcel_state = state.model_copy(
        update={"booking_type": Field(value=BookingType.PARCEL, status=FieldStatus.PROVIDED)}
    )
    assert spec_for("goods.items").is_required_now(parcel_state) is False


def test_floor_only_matters_for_physical_goods():
    state = BookingState()
    assert spec_for("pickup.floor").is_required_now(state) is False

    furniture_state = state.model_copy(
        update={
            "goods": state.goods.model_copy(
                update={
                    "category": Field(value=GoodsCategory.FURNITURE, status=FieldStatus.PROVIDED)
                }
            )
        }
    )
    assert spec_for("pickup.floor").is_required_now(furniture_state) is True


@pytest.mark.parametrize("floor,lift_required", [(0, False), (1, False), (2, True), (5, True)])
def test_lift_only_matters_at_floor_two_or_above(floor, lift_required):
    state = BookingState()
    state = state.model_copy(
        update={
            "goods": state.goods.model_copy(
                update={
                    "category": Field(value=GoodsCategory.FURNITURE, status=FieldStatus.PROVIDED)
                }
            ),
            "pickup": state.pickup.model_copy(
                update={"floor": Field[int](value=floor, status=FieldStatus.PROVIDED)}
            ),
        }
    )
    assert spec_for("pickup.has_lift").is_required_now(state) is lift_required


def test_unknown_spec_path_raises_with_a_helpful_message():
    with pytest.raises(KeyError):
        spec_for("not.a.real.field")


def test_optional_fields_have_no_spec():
    # landmark, exact_time and notes are "never asked" per design.md SS3.3 --
    # they deliberately have no FieldSpec entry at all.
    for path in ("pickup.landmark", "drop.landmark", "schedule.exact_time"):
        with pytest.raises(KeyError):
            spec_for(path)


def test_get_field_resolves_nested_and_top_level_paths():
    state = BookingState()
    assert get_field(state, "pickup.locality") is state.pickup.locality
    assert get_field(state, "booking_type") is state.booking_type


def test_get_field_rejects_a_path_that_is_not_a_field():
    with pytest.raises(TypeError):
        get_field(BookingState(), "goods.items")  # a list, not a Field


def test_with_field_is_immutable_and_precise():
    state = BookingState()
    new_locality = Field[str](value="Koramangala", status=FieldStatus.PROVIDED)

    updated = with_field(state, "pickup.locality", new_locality)

    assert state.pickup.locality.value is None  # original untouched
    assert updated.pickup.locality.value == "Koramangala"
    assert updated.drop.locality.value is None  # nothing else disturbed


def test_with_field_rejects_paths_deeper_than_the_schema():
    with pytest.raises(ValueError):
        with_field(BookingState(), "pickup.locality.extra", Field())
