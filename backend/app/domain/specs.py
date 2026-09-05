"""The declarative field specification table.

One table drives three things that would otherwise drift apart: which fields
are required, what order to ask about them in, and how to recognise a short
answer to each one. See docs/design.md SS3.3 for the field-by-field rationale.

This module also owns dotted-path field access (`get_field` / `with_field`).
The schema is a known, fixed, two-levels-deep shape -- "pickup.locality", or a
bare "booking_type" -- never an arbitrary path. A generic recursive path
resolver would solve a more general problem than this project has; a
two-level resolver is the right amount of machinery for the actual shape.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from app.domain.state import BookingState, Field, GoodsCategory


class RequirementKind(StrEnum):
    REQUIRED = "required"  # always blocks completion
    CONDITIONAL = "conditional"  # blocks completion only when `required_when` is true
    INFERRED = "inferred"  # derived by code; proposed for confirmation, never asked cold


class AnswerType(StrEnum):
    """What shape of answer a slot expects.

    Not used until phase 3's fast-path classifier, but defined on the spec now
    (rather than bolted on later) since it is a property of the *field*, not
    of the conversation logic that eventually reads it.
    """

    TEXT = "text"
    DATE = "date"
    TIME_WINDOW = "time_window"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ENUM = "enum"


@dataclass(frozen=True)
class FieldSpec:
    field_path: str
    priority: int
    kind: RequirementKind
    label: str  # human-readable name, for questions and test failure messages
    answer_type: AnswerType = AnswerType.TEXT
    required_when: Callable[[BookingState], bool] | None = None

    def is_required_now(self, state: BookingState) -> bool:
        """True if this slot currently blocks completion.

        INFERRED-kind specs are never "required" in this sense -- they are a
        separate completeness dimension (see domain/completeness.py), because
        counting them as missing would make GATHERING unable to ever finish:
        an inferred value only exists after something else already filled in
        enough information to derive it from.
        """
        if self.kind == RequirementKind.REQUIRED:
            return True
        if self.kind == RequirementKind.CONDITIONAL:
            assert self.required_when is not None, f"{self.field_path} has no required_when"
            return self.required_when(state)
        return False


# --- Conditional-requirement predicates ------------------------------------
# Kept as small named functions rather than inline lambdas so a failing test
# or an interview question can point at a specific, readable rule.


def _needs_items(state: BookingState) -> bool:
    """Every category except a parcel/document drop-off needs an item list.

    Keyed off `goods.category` rather than `booking_type`: category is a
    required field the extractor reliably populates, whereas nothing in this
    system ever sets `booking_type` on its own (see the note on it in
    docs/design.md SS3.2 -- it is an optional field the extractor may fill in
    if the user states it, used for summary framing, not for gating any
    requirement).
    """
    return state.goods.category.value != GoodsCategory.PARCEL_DOCUMENTS


def _pickup_floor_matters(state: BookingState) -> bool:
    return _goods_need_floor_handling(state)


def _drop_floor_matters(state: BookingState) -> bool:
    return _goods_need_floor_handling(state)


def _goods_need_floor_handling(state: BookingState) -> bool:
    """Floors only matter when there is physical furniture/appliances to carry."""
    category = state.goods.category.value
    return category in (
        GoodsCategory.FURNITURE,
        GoodsCategory.APPLIANCES,
        GoodsCategory.HOUSEHOLD_MIXED,
    )


def _pickup_lift_matters(state: BookingState) -> bool:
    """A lift is irrelevant below the 2nd floor -- asking anyway is form-like
    questioning, exactly what the brief contrasts against a real assistant."""
    return _goods_need_floor_handling(state) and (state.pickup.floor.value or 0) >= 2


def _drop_lift_matters(state: BookingState) -> bool:
    return _goods_need_floor_handling(state) and (state.drop.floor.value or 0) >= 2


def _needs_disassembly_check(state: BookingState) -> bool:
    """Only worth asking if a bulky item that plausibly needs disassembly is present."""
    bulky = {"bed", "wardrobe", "cupboard", "almirah", "sofa"}
    return any(any(word in item.name.lower() for word in bulky) for item in state.goods.items)


def _time_window_matters(state: BookingState) -> bool:
    """A time-of-day window is not worth asking for once the user has said "ASAP".

    `not None` is `True`, so an unset is_asap (the default) correctly leaves
    time_window required; only an explicit `is_asap=True` waives it. Found
    the same way as the booking_type bug above: docs/design.md SS3.3 already
    documented "satisfied by is_asap" as the intended rule, but nothing had
    actually implemented it -- time_window was unconditionally required, so
    a user saying "as soon as possible" would still be asked what time of
    day they meant.
    """
    return not state.schedule.is_asap.value


def _needs_packing_check(state: BookingState) -> bool:
    """Packing help is worth asking about for a mixed household load.

    Keyed off `goods.category` for the same reason as `_needs_items` above --
    "household_mixed" is a reasonable, already-reliable proxy for "this is a
    house-shifting-scale job" without depending on the never-set `booking_type`.
    """
    return state.goods.category.value == GoodsCategory.HOUSEHOLD_MIXED


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("pickup.locality", 10, RequirementKind.REQUIRED, "pickup location"),
    FieldSpec("drop.locality", 20, RequirementKind.REQUIRED, "drop location"),
    FieldSpec("goods.category", 30, RequirementKind.REQUIRED, "type of goods", AnswerType.ENUM),
    FieldSpec(
        "goods.items",
        40,
        RequirementKind.CONDITIONAL,
        "items to move",
        required_when=_needs_items,
    ),
    FieldSpec("schedule.date", 50, RequirementKind.REQUIRED, "date", AnswerType.DATE),
    FieldSpec(
        "schedule.time_window",
        55,
        RequirementKind.CONDITIONAL,
        "time of day",
        AnswerType.TIME_WINDOW,
        required_when=_time_window_matters,
    ),
    FieldSpec(
        "pickup.floor",
        60,
        RequirementKind.CONDITIONAL,
        "pickup floor",
        AnswerType.INTEGER,
        required_when=_pickup_floor_matters,
    ),
    FieldSpec(
        "pickup.has_lift",
        65,
        RequirementKind.CONDITIONAL,
        "lift at pickup",
        AnswerType.BOOLEAN,
        required_when=_pickup_lift_matters,
    ),
    FieldSpec(
        "drop.floor",
        70,
        RequirementKind.CONDITIONAL,
        "drop floor",
        AnswerType.INTEGER,
        required_when=_drop_floor_matters,
    ),
    FieldSpec(
        "drop.has_lift",
        75,
        RequirementKind.CONDITIONAL,
        "lift at drop",
        AnswerType.BOOLEAN,
        required_when=_drop_lift_matters,
    ),
    FieldSpec(
        "service.needs_disassembly",
        80,
        RequirementKind.CONDITIONAL,
        "disassembly needed",
        AnswerType.BOOLEAN,
        required_when=_needs_disassembly_check,
    ),
    FieldSpec(
        "service.needs_packing",
        85,
        RequirementKind.CONDITIONAL,
        "packing needed",
        AnswerType.BOOLEAN,
        required_when=_needs_packing_check,
    ),
    FieldSpec(
        "service.vehicle_type", 90, RequirementKind.INFERRED, "vehicle type", AnswerType.ENUM
    ),
    FieldSpec(
        "service.helpers_required",
        95,
        RequirementKind.INFERRED,
        "helpers required",
        AnswerType.INTEGER,
    ),
)

_SPECS_BY_PATH: dict[str, FieldSpec] = {spec.field_path: spec for spec in FIELD_SPECS}


def spec_for(field_path: str) -> FieldSpec:
    try:
        return _SPECS_BY_PATH[field_path]
    except KeyError:
        raise KeyError(
            f"{field_path!r} has no FieldSpec. Optional fields (landmark, exact_time, "
            "notes) are never asked about and deliberately have no spec entry."
        ) from None


# --- Dotted-path field access ------------------------------------------------


def get_field(state: BookingState, path: str) -> Field:
    """Resolve a dotted path like 'pickup.locality' or 'booking_type' to its Field[T]."""
    obj: object = state
    for part in path.split("."):
        obj = getattr(obj, part)
    if not isinstance(obj, Field):
        raise TypeError(f"{path!r} does not resolve to a Field (got {type(obj).__name__})")
    return obj


def with_field(state: BookingState, path: str, new_field: Field) -> BookingState:
    """Return a new BookingState with the Field at `path` replaced.

    Immutable: `state` is never touched. Handles exactly the two shapes that
    occur in this schema -- a bare top-level path ("booking_type") and a
    one-level-nested path ("pickup.locality") -- because that is the entire
    shape of BookingState. See the module docstring for why this does not try
    to be a general-purpose path library.
    """
    parts = path.split(".")
    if len(parts) == 1:
        return state.model_copy(update={parts[0]: new_field})
    if len(parts) == 2:
        top, leaf = parts
        nested = getattr(state, top)
        new_nested = nested.model_copy(update={leaf: new_field})
        return state.model_copy(update={top: new_nested})
    raise ValueError(f"{path!r} is deeper than this schema supports (max 2 levels)")
