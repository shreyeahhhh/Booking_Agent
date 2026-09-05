"""Applying proposed changes to the booking state.

This is the one function in the whole system that is allowed to change a
BookingState (see docs/architecture.md, "Thesis"). Everything it does is
deterministic: no network call, no randomness, no dependence on anything
except its own arguments. Feeding it the same state and patches twice always
produces the same result -- which is exactly what makes it possible to test
correction handling and out-of-order input with zero AI involved, per
MASTER_PLAN.md phase 1's acceptance criterion.

Three rules, straight from docs/design.md SS5.3, are enforced here and nowhere
else:

1. `op: set` against a CONFIRMED field is rejected as a conflict, not applied.
2. `op: correct` always replaces, and pushes the old value into `revisions`.
3. Changing the item list invalidates any *system-guessed* vehicle/helper
   count so it gets re-derived -- but never an explicit user choice; see
   `_recompute_derived_fields` below for why that distinction matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime

from app.domain.inference import infer_vehicle_and_helpers
from app.domain.normalizers import resolve_quantity_word, resolve_relative_date, resolve_time_of_day
from app.domain.specs import get_field, with_field
from app.domain.state import (
    AmbiguityReason,
    BookingState,
    Conflict,
    Field,
    FieldStatus,
    Item,
    Patch,
    PatchOp,
    Revision,
    VehicleType,
)

# Bare city names, with no locality qualifier, that are too coarse to route a
# pickup or drop to. Deliberately a short, hardcoded list rather than a
# geocoding lookup -- see README's "Assumptions and limitations": this is a
# heuristic, not gazetteer-backed place validation.
_BARE_CITY_NAMES = {
    "bangalore",
    "bengaluru",
    "kochi",
    "cochin",
    "mumbai",
    "chennai",
    "delhi",
    "new delhi",
    "hyderabad",
    "pune",
    "kolkata",
    "ahmedabad",
}


@dataclass
class ReducerResult:
    state: BookingState
    events: list[str] = dataclass_field(default_factory=list)


def apply(
    state: BookingState,
    patches: list[Patch],
    *,
    reference: datetime | None = None,
) -> ReducerResult:
    """Apply every patch from one utterance and return the resulting state.

    `reference` is "now", used for relative-date resolution. Tests pass a
    fixed value so date logic is deterministic; production code leaves it as
    the real current time.
    """
    reference = reference or datetime.now()
    turn = state.turn + 1
    state = state.model_copy(update={"turn": turn})
    events: list[str] = []

    for patch in patches:
        state, patch_events = _apply_one(state, patch, turn, reference)
        events.extend(patch_events)

    return ReducerResult(state=state, events=events)


def confirm_all(state: BookingState, turn: int | None = None) -> BookingState:
    """Flip every PROVIDED or INFERRED field to CONFIRMED.

    Called when the user affirms the final summary (a REVIEW-phase action, not
    a per-field patch -- see docs/design.md SS5.1). Individual patches are
    about *extracting* information; this is the separate, explicit act of
    accepting the summary as a whole.
    """
    turn = turn if turn is not None else state.turn

    def confirm(f: Field) -> Field:
        if f.status in (FieldStatus.PROVIDED, FieldStatus.INFERRED):
            return f.model_copy(update={"status": FieldStatus.CONFIRMED})
        return f

    new_pickup = state.pickup.model_copy(
        update={
            "raw_text": confirm(state.pickup.raw_text),
            "locality": confirm(state.pickup.locality),
            "city": confirm(state.pickup.city),
            "landmark": confirm(state.pickup.landmark),
            "floor": confirm(state.pickup.floor),
            "has_lift": confirm(state.pickup.has_lift),
        }
    )
    new_drop = state.drop.model_copy(
        update={
            "raw_text": confirm(state.drop.raw_text),
            "locality": confirm(state.drop.locality),
            "city": confirm(state.drop.city),
            "landmark": confirm(state.drop.landmark),
            "floor": confirm(state.drop.floor),
            "has_lift": confirm(state.drop.has_lift),
        }
    )
    new_schedule = state.schedule.model_copy(
        update={
            "date": confirm(state.schedule.date),
            "time_window": confirm(state.schedule.time_window),
            "exact_time": confirm(state.schedule.exact_time),
            "is_asap": confirm(state.schedule.is_asap),
        }
    )
    new_goods = state.goods.model_copy(
        update={
            "category": confirm(state.goods.category),
            "load_hint": confirm(state.goods.load_hint),
        }
    )
    new_service = state.service.model_copy(
        update={
            "vehicle_type": confirm(state.service.vehicle_type),
            "helpers_required": confirm(state.service.helpers_required),
            "needs_packing": confirm(state.service.needs_packing),
            "needs_disassembly": confirm(state.service.needs_disassembly),
        }
    )
    return state.model_copy(
        update={
            "turn": turn,
            "booking_type": confirm(state.booking_type),
            "pickup": new_pickup,
            "drop": new_drop,
            "schedule": new_schedule,
            "goods": new_goods,
            "service": new_service,
        }
    )


# --------------------------------------------------------------------------
# Per-patch application
# --------------------------------------------------------------------------


_TIME_FIELDS = ("schedule.time_window", "schedule.exact_time")
_WRITABLE_OPS = (PatchOp.SET, PatchOp.CORRECT)


def _apply_one(
    state: BookingState, patch: Patch, turn: int, reference: datetime
) -> tuple[BookingState, list[str]]:
    if patch.field == "goods.items":
        return _apply_items_patch(state, patch, turn)

    is_normalizable_time_patch = (
        patch.field in _TIME_FIELDS and patch.needs_normalization and patch.op in _WRITABLE_OPS
    )
    if is_normalizable_time_patch:
        return _apply_time_patch(state, patch, turn)

    try:
        current = get_field(state, patch.field)
    except (AttributeError, TypeError, KeyError):
        return state, [f"skipped patch for unresolvable field {patch.field!r}"]

    if patch.op not in (PatchOp.SET, PatchOp.CORRECT, PatchOp.CLEAR):
        return state, [f"skipped {patch.op} on scalar field {patch.field!r}"]

    if patch.op == PatchOp.CLEAR:
        return with_field(state, patch.field, Field()), [f"cleared {patch.field}"]

    value, ambiguity, norm_event = _resolve_value(patch, reference)
    events = [norm_event] if norm_event else []

    if ambiguity is None and value is not None:
        ambiguity = _validate(patch.field, value)

    new_state, write_events = _write_scalar(
        state,
        patch.field,
        patch.op,
        value=value,
        ambiguity=ambiguity,
        confidence=patch.confidence,
        evidence=patch.evidence,
        turn=turn,
        current=current,
    )
    return new_state, events + write_events


def _write_scalar(
    state: BookingState,
    field_path: str,
    op: PatchOp,
    *,
    value: object | None,
    ambiguity: AmbiguityReason | None,
    confidence: float,
    evidence: str | None,
    turn: int,
    current: Field,
) -> tuple[BookingState, list[str]]:
    """Write one resolved value to one Field, enforcing the correction rules.

    Factored out of `_apply_one` so `_apply_time_patch` can reuse the exact
    same CONFIRMED-guard, revision-history and conflict-clearing behaviour
    when it writes to *two* fields (time_window and exact_time) from one
    resolved time phrase, rather than re-implementing those rules a second
    time with room for the two copies to drift apart.
    """
    if op == PatchOp.SET and current.status == FieldStatus.CONFIRMED:
        conflict = Conflict(
            field=field_path, existing_value=current.value, attempted_value=value, turn=turn
        )
        new_state = state.model_copy(update={"conflicts": [*state.conflicts, conflict]})
        return new_state, [
            f"conflict on {field_path}: confirmed {current.value!r}, attempted {value!r}"
        ]

    new_status = FieldStatus.AMBIGUOUS if ambiguity else FieldStatus.PROVIDED
    new_field = Field(
        value=value,
        status=new_status,
        confidence=min(confidence, 0.5) if ambiguity else confidence,
        evidence=evidence,
        turn=turn,
        clarify_attempts=current.clarify_attempts if ambiguity else 0,
        ambiguity=ambiguity,
        revisions=current.revisions,
    )

    if op == PatchOp.CORRECT:
        if current.status != FieldStatus.EMPTY:
            revision = Revision(
                value=current.value,
                status=current.status,
                evidence=current.evidence,
                turn=current.turn,
            )
            new_field = new_field.model_copy(update={"revisions": [*current.revisions, revision]})
        # A correction explicitly resolves any pending conflict on this field.
        remaining_conflicts = [c for c in state.conflicts if c.field != field_path]
        state = state.model_copy(update={"conflicts": remaining_conflicts})

    new_state = with_field(state, field_path, new_field)
    suffix = " (ambiguous)" if ambiguity else ""
    return new_state, [f"{op} {field_path} -> {value!r}{suffix}"]


def _apply_time_patch(
    state: BookingState, patch: Patch, turn: int
) -> tuple[BookingState, list[str]]:
    """A time phrase can fill both the coarse window and the exact time at once.

    "4:30pm" answers "what time of day" just as much as it answers "what
    exact time" -- a user who gives an exact time should never then be asked
    the coarser question too. Both sub-fields are written through
    `_write_scalar` so each still independently respects the CONFIRMED-guard
    and revision history.
    """
    resolution = resolve_time_of_day(str(patch.value))
    if resolution.time_window is None and resolution.exact_time is None:
        current = get_field(state, patch.field)
        new_state, events = _write_scalar(
            state,
            patch.field,
            patch.op,
            value=patch.value,
            ambiguity=AmbiguityReason.VAGUE_TIME,
            confidence=patch.confidence,
            evidence=patch.evidence,
            turn=turn,
            current=current,
        )
        return new_state, [f"could not resolve time phrase {patch.value!r}", *events]

    events: list[str] = [f"resolved {patch.value!r} -> {resolution}"]
    if resolution.time_window is not None:
        current = get_field(state, "schedule.time_window")
        state, sub_events = _write_scalar(
            state,
            "schedule.time_window",
            patch.op,
            value=resolution.time_window,
            ambiguity=None,
            confidence=patch.confidence,
            evidence=patch.evidence,
            turn=turn,
            current=current,
        )
        events.extend(sub_events)
    if resolution.exact_time is not None:
        current = get_field(state, "schedule.exact_time")
        state, sub_events = _write_scalar(
            state,
            "schedule.exact_time",
            patch.op,
            value=resolution.exact_time,
            ambiguity=None,
            confidence=patch.confidence,
            evidence=patch.evidence,
            turn=turn,
            current=current,
        )
        events.extend(sub_events)
    return state, events


def _resolve_value(
    patch: Patch, reference: datetime
) -> tuple[object | None, AmbiguityReason | None, str | None]:
    """Normalise a patch's raw value if it needs it; otherwise pass it through.

    Returns (value, ambiguity, event). `ambiguity` is set when normalisation
    fails -- the safe outcome (ask again) rather than storing nothing or
    guessing, per docs/design.md's system prompt Rule 3.
    """
    if patch.ambiguity is not None:
        return patch.value, patch.ambiguity, None

    if not patch.needs_normalization:
        return patch.value, None, None

    raw = str(patch.value)

    if patch.field == "schedule.date":
        resolved = resolve_relative_date(raw, reference)
        if resolved is None:
            return raw, AmbiguityReason.RELATIVE_DATE, f"could not resolve date phrase {raw!r}"
        return resolved.isoformat(), None, f"resolved {raw!r} -> {resolved.isoformat()}"

    # schedule.time_window / schedule.exact_time are handled by
    # _apply_time_patch before a call ever reaches here (see _apply_one) --
    # a time phrase can resolve both sub-fields at once, which a single
    # (value, ambiguity, event) return here could not express.

    return patch.value, None, None


def _validate(field_path: str, value: object) -> AmbiguityReason | None:
    """Independent ambiguity detection, run regardless of what the extractor said.

    See docs/design.md SS5.4: the model flagging ambiguity is one of three
    sources, not the only one.
    """
    is_bare_city = (
        field_path.endswith(".locality")
        and isinstance(value, str)
        and value.strip().lower() in _BARE_CITY_NAMES
    )
    if is_bare_city:
        return AmbiguityReason.CITY_LEVEL_ONLY
    return None


# --------------------------------------------------------------------------
# The item list
# --------------------------------------------------------------------------


def _apply_items_patch(
    state: BookingState, patch: Patch, turn: int
) -> tuple[BookingState, list[str]]:
    items = state.goods.items

    if patch.op == PatchOp.APPEND:
        item = _coerce_item(patch.value, patch.evidence, turn)
        if item is None:
            return state, [f"skipped malformed item append: {patch.value!r}"]
        new_items = [*items, item]
        return _finish_items_change(state, new_items, turn, f"added item {item.name!r}")

    if patch.op == PatchOp.CORRECT:
        payload = patch.value if isinstance(patch.value, dict) else {}
        name = str(payload.get("name", "")).strip().lower()
        matched = next((i for i, it in enumerate(items) if it.name.strip().lower() == name), None)
        if matched is None:
            item = _coerce_item(patch.value, patch.evidence, turn)
            if item is None:
                return state, [f"skipped malformed item correction: {patch.value!r}"]
            return _finish_items_change(state, [*items, item], turn, f"added item {item.name!r}")
        existing = items[matched]
        updated = existing.model_copy(
            update={
                "quantity": payload.get("quantity", existing.quantity),
                "size_hint": payload.get("size_hint", existing.size_hint),
                "evidence": patch.evidence or existing.evidence,
                "turn": turn,
            }
        )
        new_items = [*items[:matched], updated, *items[matched + 1 :]]
        event = f"corrected item {existing.name!r} -> quantity {updated.quantity}"
        return _finish_items_change(state, new_items, turn, event)

    if patch.op == PatchOp.REMOVE:
        target = patch.value.get("name") if isinstance(patch.value, dict) else patch.value
        target = str(target or "").strip().lower()
        new_items = [i for i in items if i.name.strip().lower() != target]
        return _finish_items_change(state, new_items, turn, f"removed item matching {target!r}")

    if patch.op == PatchOp.CLEAR:
        return _finish_items_change(state, [], turn, "cleared all items")

    return state, [f"skipped {patch.op} on goods.items (unsupported for the list as a whole)"]


def _coerce_item(raw: object, evidence: str | None, turn: int) -> Item | None:
    if isinstance(raw, str) and raw.strip():
        return Item(name=raw.strip(), quantity=1, evidence=evidence, turn=turn)
    if isinstance(raw, dict) and raw.get("name"):
        quantity = raw.get("quantity", 1)
        if isinstance(quantity, str):
            quantity = resolve_quantity_word(quantity) or 1
        return Item(
            name=str(raw["name"]).strip(),
            quantity=int(quantity),
            size_hint=raw.get("size_hint"),
            evidence=evidence,
            turn=turn,
        )
    return None


def _finish_items_change(
    state: BookingState, new_items: list[Item], turn: int, event: str
) -> tuple[BookingState, list[str]]:
    new_goods = state.goods.model_copy(update={"items": new_items})
    state = state.model_copy(update={"goods": new_goods})
    state = _recompute_derived_fields(state, turn)
    return state, [event]


def _recompute_derived_fields(state: BookingState, turn: int) -> BookingState:
    """Re-derive vehicle/helper guesses after the item list changes.

    Only ever overwrites a field that is still a system guess (EMPTY or
    INFERRED). A field the user explicitly stated or confirmed themselves is
    left untouched -- otherwise adding one more box after the user said
    "actually, send a pickup truck" would silently discard their choice,
    which is exactly the "overwriting correct information" failure mode this
    project is built to avoid. See docs/architecture.md, "Corrections".

    This recomputes from scratch rather than first "invalidating" and then
    re-inferring as two steps -- fetching a fresh guess and overwriting *is*
    the invalidation, with no separate staleness bookkeeping required.
    """
    guess = infer_vehicle_and_helpers(state.goods.items)
    service = state.service
    updates: dict[str, Field] = {}

    if service.vehicle_type.status in (FieldStatus.EMPTY, FieldStatus.INFERRED):
        updates["vehicle_type"] = (
            Field[VehicleType](
                value=guess.vehicle_type, status=FieldStatus.INFERRED, confidence=1.0, turn=turn
            )
            if guess
            else Field()
        )
    if service.helpers_required.status in (FieldStatus.EMPTY, FieldStatus.INFERRED):
        updates["helpers_required"] = (
            Field[int](
                value=guess.helpers_required,
                status=FieldStatus.INFERRED,
                confidence=1.0,
                turn=turn,
            )
            if guess
            else Field()
        )

    if not updates:
        return state
    return state.model_copy(update={"service": state.service.model_copy(update=updates)})
