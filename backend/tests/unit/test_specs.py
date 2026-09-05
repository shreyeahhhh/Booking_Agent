"""FieldSpec predicates and dotted-path access -- MASTER_PLAN.md step 1.2."""

import pytest

from app.domain.specs import FIELD_SPECS, get_field, spec_for, with_field
from app.domain.state import BookingState, Field, FieldStatus, GoodsCategory


def test_every_priority_is_unique():
    priorities = [spec.priority for spec in FIELD_SPECS]
    assert len(priorities) == len(set(priorities))


def test_unconditional_required_fields_are_always_required():
    for path in ("pickup.locality", "drop.locality", "goods.items", "schedule.date"):
        assert spec_for(path).is_required_now(BookingState()) is True


def test_goods_category_has_no_spec_of_its_own():
    """It is inferred from items (domain.inference.infer_category), never
    asked about directly -- see _goods_need_floor_handling's docstring for
    why a category the model can only fill by saying a category word out
    loud would get permanently stuck."""
    with pytest.raises(KeyError):
        spec_for("goods.category")


def test_time_window_defaults_to_required_but_is_asap_waives_it():
    """schedule.time_window is CONDITIONAL, not REQUIRED, specifically so
    "as soon as possible" can satisfy it -- see specs.py's
    _time_window_matters docstring for the bug this fixes."""
    state = BookingState()
    assert spec_for("schedule.time_window").is_required_now(state) is True

    asap_state = state.model_copy(
        update={"schedule": state.schedule.model_copy(update={"is_asap": Field(value=True)})}
    )
    assert spec_for("schedule.time_window").is_required_now(asap_state) is False

    explicitly_not_asap = state.model_copy(
        update={"schedule": state.schedule.model_copy(update={"is_asap": Field(value=False)})}
    )
    assert spec_for("schedule.time_window").is_required_now(explicitly_not_asap) is True


def test_items_are_required_regardless_of_category():
    """Unconditional on purpose: even a parcel drop-off is just an item like
    "documents", so there is no category state -- including one that does
    not exist yet, since category is *inferred from* items -- that should
    ever waive this. A conditional-on-category version of this requirement
    was the original design and created a circular dependency (items needed
    category; category needed items) that this sidesteps entirely."""
    state = BookingState()
    assert spec_for("goods.items").is_required_now(state) is True

    parcel_state = state.model_copy(
        update={
            "goods": state.goods.model_copy(
                update={
                    "category": Field(
                        value=GoodsCategory.PARCEL_DOCUMENTS, status=FieldStatus.PROVIDED
                    )
                }
            )
        }
    )
    assert spec_for("goods.items").is_required_now(parcel_state) is True


def test_packing_only_matters_for_a_mixed_household_load():
    state = BookingState()
    assert spec_for("service.needs_packing").is_required_now(state) is False

    mixed_state = state.model_copy(
        update={
            "goods": state.goods.model_copy(
                update={
                    "category": Field(
                        value=GoodsCategory.HOUSEHOLD_MIXED, status=FieldStatus.PROVIDED
                    )
                }
            )
        }
    )
    assert spec_for("service.needs_packing").is_required_now(mixed_state) is True

    furniture_state = state.model_copy(
        update={
            "goods": state.goods.model_copy(
                update={
                    "category": Field(value=GoodsCategory.FURNITURE, status=FieldStatus.PROVIDED)
                }
            )
        }
    )
    assert spec_for("service.needs_packing").is_required_now(furniture_state) is False


def test_booking_type_never_gates_any_requirement():
    """Regression guard: booking_type has no FieldSpec of its own and nothing
    in this system ever sets it, so no predicate may depend on it -- if one
    does, every state where it is unset would silently misbehave forever.

    Checks for the attribute-access pattern specifically (".booking_type"),
    not the bare word, since docstrings legitimately mention it by name when
    explaining why a predicate does NOT use it (see _needs_items above).
    """
    import inspect

    predicate_sources = "".join(
        inspect.getsource(spec.required_when)
        for spec in FIELD_SPECS
        if spec.required_when is not None
    )
    assert ".booking_type" not in predicate_sources


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


def test_all_scalar_paths_has_no_duplicates_and_every_path_resolves():
    from app.domain.specs import ALL_SCALAR_PATHS

    assert len(ALL_SCALAR_PATHS) == len(set(ALL_SCALAR_PATHS))
    state = BookingState()
    for path in ALL_SCALAR_PATHS:
        get_field(state, path)  # raises if any path is not a real Field


def test_all_scalar_paths_is_exactly_field_specs_plus_optional_paths():
    from app.domain.specs import ALL_SCALAR_PATHS, OPTIONAL_SCALAR_PATHS

    spec_paths = {spec.field_path for spec in FIELD_SPECS if spec.field_path != "goods.items"}
    assert set(ALL_SCALAR_PATHS) == spec_paths | set(OPTIONAL_SCALAR_PATHS)


def test_field_class_resolves_every_scalar_path_and_coerces_correctly():
    """Regression guard for the bug a live conversation test caught: a value
    built via bare `Field(...)` is never validated against its declared type
    (Pydantic has no bound type parameter to coerce against), so an enum
    field silently stored a raw string instead of the real enum member.
    field_class(path) must resolve to the *parameterised* class so
    constructing through it actually coerces."""
    from app.domain.specs import ALL_SCALAR_PATHS, field_class
    from app.domain.state import FieldStatus, GoodsCategory

    for path in ALL_SCALAR_PATHS:
        cls = field_class(path)
        assert issubclass(cls, Field)

    category_field = field_class("goods.category")(value="furniture", status=FieldStatus.PROVIDED)
    assert category_field.value is GoodsCategory.FURNITURE
    assert isinstance(category_field.value, GoodsCategory)


def test_field_class_rejects_a_path_that_is_not_a_scalar_field():
    from app.domain.specs import field_class

    with pytest.raises(TypeError):
        field_class("goods.items")  # a list, not a Field[T]
