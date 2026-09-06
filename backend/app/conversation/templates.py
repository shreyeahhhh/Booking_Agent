"""Every sentence the agent speaks -- MASTER_PLAN.md step 2.4.

No LLM involved anywhere in this file. That is the whole point of the "LLM
is a sensor, not a controller" design (docs/architecture.md): the model
proposes patches, and everything the user actually hears is assembled here,
from the resulting state, by plain string composition.

Responses compose as:

    acknowledgment(what just landed) + question(next_slot, state)

rather than the three-part acknowledgment/correction-note/question split
`docs/design.md` SS5.2 originally sketched: a correction reads naturally as
part of the acknowledgment itself ("Got it, updated to Saturday") rather
than needing a bolted-on clause, so this collapses that into a single
lead-in phrase that varies by whether a correction happened this turn --
one fewer moving part, same information conveyed.

What counts as "just landed" is read directly off the state, not off the
raw patches the extractor proposed: a `Field` whose `.turn` equals the
state's current turn was actually written this turn, which is exactly what
distinguishes an accepted change from one the reducer rejected as a
conflict (a rejected field's turn never advances). The original Patch list
is still needed alongside this to know *how* it changed (its `op`), since
turn number alone cannot distinguish a correction from a fresh value.
"""

from __future__ import annotations

from datetime import date

from app.domain.policy import SlotDecision, SlotReason
from app.domain.specs import get_field, spec_for
from app.domain.state import (
    AmbiguityReason,
    BookingState,
    Item,
    Patch,
    PatchOp,
    VehicleType,
)

# --------------------------------------------------------------------------
# Value formatters -- turning a raw stored value into what gets said aloud
# --------------------------------------------------------------------------

_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def format_floor(floor: int) -> str:
    if floor == 0:
        return "the ground floor"
    suffix = _ORDINAL_SUFFIX.get(floor if floor < 20 else floor % 10, "th")
    return f"the {floor}{suffix} floor"


def format_date_short(iso_date: str) -> str:
    """Weekday only -- "Saturday". For mid-conversation acknowledgments and
    questions: "Saturday, 12 September" is precise but reads over-formal
    said aloud mid-sentence. The fuller form is for the final summary
    (step 2.6), where written precision matters more than spoken brevity.
    """
    return date.fromisoformat(iso_date).strftime("%A")


def format_date_full(iso_date: str) -> str:
    """Weekday and calendar date -- "Saturday, 12 September".

    For the final summary (step 2.6), not spoken acknowledgments.
    """
    d = date.fromisoformat(iso_date)
    return d.strftime("%A, %d %B").replace(" 0", " ")


_VEHICLE_NAMES = {
    VehicleType.TWO_WHEELER: "a two-wheeler",
    VehicleType.THREE_WHEELER: "a three-wheeler",
    VehicleType.TATA_ACE: "a Tata Ace",
    VehicleType.PICKUP_8FT: "an 8-foot pickup truck",
    VehicleType.TEMPO_14FT: "a 14-foot tempo",
}


def format_vehicle(vehicle: VehicleType) -> str:
    return _VEHICLE_NAMES[vehicle]


def format_item(item: Item) -> str:
    if item.quantity <= 1:
        return f"a {item.name}"
    return f"{item.quantity} {item.name}s"


def format_item_list(items: list[Item]) -> str:
    words = [format_item(i) for i in items]
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + f" and {words[-1]}"


# --------------------------------------------------------------------------
# Acknowledging what just landed
# --------------------------------------------------------------------------


def _landed_this_turn(state: BookingState, patches: list[Patch]) -> list[Patch]:
    """Patches whose target actually changed in `state` this turn.

    Excludes a patch the reducer rejected as a conflict against a CONFIRMED
    field -- that field's `.turn` never advanced, so it is correctly left
    for the next question (a conflict), not acknowledged as accepted.
    """
    landed = []
    for patch in patches:
        if patch.field == "goods.items":
            if state.goods.items and state.goods.items[-1].turn == state.turn:
                landed.append(patch)
            continue
        if patch.field == "notes":
            if state.notes and state.notes[-1].turn == state.turn:
                landed.append(patch)
            continue
        field = get_field(state, patch.field)
        if field.turn == state.turn:
            landed.append(patch)
    return landed


def _location_fragment(state: BookingState, changed_paths: set[str]) -> str | None:
    pickup_changed = "pickup.locality" in changed_paths
    drop_changed = "drop.locality" in changed_paths
    pickup = get_field(state, "pickup.locality").value
    drop = get_field(state, "drop.locality").value

    if pickup_changed and drop_changed:
        return f"{pickup} to {drop}"
    if pickup_changed:
        return f"picking up from {pickup}"
    if drop_changed:
        return f"dropping off at {drop}"
    return None


def _schedule_fragment(state: BookingState, changed_paths: set[str]) -> str | None:
    date_changed = "schedule.date" in changed_paths
    window_changed = (
        "schedule.time_window" in changed_paths or "schedule.exact_time" in changed_paths
    )
    asap_changed = "schedule.is_asap" in changed_paths

    if asap_changed and get_field(state, "schedule.is_asap").value:
        return "as soon as possible"

    date_text = format_date_short(get_field(state, "schedule.date").value) if date_changed else None
    window_value = get_field(state, "schedule.time_window").value
    window_text = window_value.value if window_changed and window_value else None

    if date_text and window_text:
        return f"{date_text}, {window_text}"
    return date_text or window_text


def _items_fragment(state: BookingState, changed_paths: set[str]) -> str | None:
    if "goods.items" not in changed_paths:
        return None
    return format_item_list(state.goods.items)


def _simple_fragment(state: BookingState, path: str) -> str | None:
    if path in ("pickup.floor", "drop.floor"):
        return format_floor(get_field(state, path).value)
    if path == "pickup.has_lift":
        return "there's a lift" if get_field(state, path).value else "no lift"
    if path == "drop.has_lift":
        return (
            "there's a lift at drop-off" if get_field(state, path).value else "no lift at drop-off"
        )
    if path == "service.needs_packing":
        return "packing help" if get_field(state, path).value else "no packing help needed"
    if path == "service.needs_disassembly":
        return "disassembly needed" if get_field(state, path).value else "no disassembly needed"
    if path == "goods.category":
        return get_field(state, path).value.value.replace("_", " ")
    if path == "booking_type":
        return get_field(state, path).value.value.replace("_", " ")
    return None


def compose_acknowledgment(patches: list[Patch], state: BookingState) -> str | None:
    """One sentence acknowledging what was just learned, or None if there is
    nothing worth acknowledging (e.g. the turn only produced unresolved
    mentions, or everything attempted was rejected as a conflict)."""
    landed = _landed_this_turn(state, patches)
    if not landed:
        return None

    changed_paths = {p.field for p in landed}
    is_correction = any(p.op == PatchOp.CORRECT for p in landed)

    fragments: list[str] = []
    location = _location_fragment(state, changed_paths)
    if location:
        fragments.append(location)
    schedule = _schedule_fragment(state, changed_paths)
    if schedule:
        fragments.append(schedule)
    items = _items_fragment(state, changed_paths)
    if items:
        fragments.append(items)

    handled = {
        "pickup.locality",
        "drop.locality",
        "schedule.date",
        "schedule.time_window",
        "schedule.exact_time",
        "schedule.is_asap",
        "goods.items",
    }
    if items:
        # Naming the actual items ("a sofa and 2 cupboards") already conveys
        # what is being moved -- restating the category too ("...furniture")
        # is redundant information no one would say aloud in a real answer.
        handled = handled | {"goods.category"}
    for path in changed_paths - handled:
        fragment = _simple_fragment(state, path)
        if fragment:
            fragments.append(fragment)

    if not fragments:
        return None

    lead_in = "Got it, updated" if is_correction else "Got it"
    return f"{lead_in} — {', '.join(fragments)}."


# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------


def _pick(variants: list[str], state: BookingState) -> str:
    """Deterministic rotation: the state's own turn number picks the variant,
    so nothing repeats verbatim on consecutive asks without needing any
    separate "which variant have we used" state to track."""
    return variants[state.turn % len(variants)]


_QUESTIONS: dict[str, list[str]] = {
    "pickup.locality": [
        "Where should we pick this up from?",
        "What's the pickup location?",
        "Where are we picking up from?",
    ],
    "drop.locality": [
        "And where's it headed?",
        "Where should we drop this off?",
        "What's the drop-off location?",
    ],
    "goods.category": [
        "What are we moving — furniture, appliances, or something else?",
        "What kind of things need to be moved?",
        "What type of items are these — furniture, appliances, or general household stuff?",
    ],
    "goods.items": [
        "What items need to go?",
        "What exactly are we moving?",
        "Tell me what needs to be moved.",
    ],
    "schedule.date": [
        "What date works for you?",
        "When would you like this done?",
        "What day should we schedule this for?",
    ],
    "schedule.time_window": [
        "What time of day — morning, afternoon, or evening?",
        "Any preferred time of day?",
        "Morning, afternoon, or evening?",
    ],
    "service.needs_disassembly": [
        "Does anything need to be taken apart first — like a bed frame?",
        "Will any furniture need disassembling before the move?",
        "Anything that needs to come apart before moving day?",
    ],
    "service.needs_packing": [
        "Would you like help with packing too?",
        "Do you need packing assistance as well?",
        "Should we include packing help?",
    ],
}


def _floor_question(path: str, state: BookingState) -> str:
    locality_path = "pickup.locality" if path == "pickup.floor" else "drop.locality"
    locality = get_field(state, locality_path).value
    side = "pickup" if path == "pickup.floor" else "drop-off"
    variants = (
        [f"Which floor is the {side}, in {locality}?", f"What floor in {locality}?"]
        if locality
        else [f"Which floor is the {side} on?", "What floor is that on?"]
    )
    return _pick(variants, state)


def _lift_question(path: str, state: BookingState) -> str:
    locality_path = "pickup.locality" if path == "pickup.has_lift" else "drop.locality"
    locality = get_field(state, locality_path).value
    variants = (
        [f"Is there a lift at {locality}?", f"Does {locality} have a lift?"]
        if locality
        else ["Is there a lift there?", "Is a lift available?"]
    )
    return _pick(variants, state)


_AMBIGUITY_QUESTIONS: dict[AmbiguityReason, str] = {
    AmbiguityReason.CITY_LEVEL_ONLY: "Which area of {value} — any locality or landmark?",
    AmbiguityReason.VAGUE_LOCATION: "Could you be a bit more specific about the location?",
    AmbiguityReason.RELATIVE_DATE: "Sorry, what date exactly did you mean?",
    AmbiguityReason.VAGUE_TIME: "What time roughly — morning, afternoon, or evening?",
    AmbiguityReason.VAGUE_QUANTITY: "Roughly how many, would you say?",
    AmbiguityReason.UNKNOWN_ITEM: "Sorry, what was that item again?",
    AmbiguityReason.CONFLICTING: "That doesn't quite match what you said before — which is right?",
}


def compose_question(decision: SlotDecision, state: BookingState) -> str:
    """The question for a policy decision that is not None.

    Callers check `decision is None` themselves (that means nothing is left
    to ask -- conversation/machine.py routes to the summary instead, not to
    this function).
    """
    if decision.reason == SlotReason.CONFLICT:
        return _conflict_question(decision, state)
    if decision.reason == SlotReason.AMBIGUOUS:
        return _ambiguity_question(decision, state)
    if decision.reason == SlotReason.CONFIRM_INFERRED:
        return _confirm_inferred_question(state)

    if decision.field_path in ("pickup.floor", "drop.floor"):
        return _floor_question(decision.field_path, state)
    if decision.field_path in ("pickup.has_lift", "drop.has_lift"):
        return _lift_question(decision.field_path, state)

    variants = _QUESTIONS.get(decision.field_path)
    if variants is None:
        # Every field the policy can select has a spec label; falling back
        # to it keeps this total rather than raising mid-conversation for a
        # field a future schema change forgot to add phrasing for.
        return f"Could you tell me the {spec_for(decision.field_path).label}?"
    return _pick(variants, state)


def _conflict_question(decision: SlotDecision, state: BookingState) -> str:
    conflict = next(c for c in state.conflicts if c.field == decision.field_path)
    return (
        f"Just to confirm — earlier you said {conflict.existing_value} for the "
        f"{decision.label}, but just now you said {conflict.attempted_value}. Which is right?"
    )


def _ambiguity_question(decision: SlotDecision, state: BookingState) -> str:
    field = get_field(state, decision.field_path)
    template = _AMBIGUITY_QUESTIONS.get(field.ambiguity, "Could you clarify that for me?")
    return template.format(value=field.value)


def _confirm_inferred_question(state: BookingState) -> str:
    vehicle = get_field(state, "service.vehicle_type").value
    helpers = get_field(state, "service.helpers_required").value
    vehicle_text = format_vehicle(vehicle)
    helper_text = (
        "no extra helpers" if helpers == 0 else f"{helpers} helper{'s' if helpers != 1 else ''}"
    )
    variants = [
        f"Based on what you're moving, {vehicle_text} with {helper_text} "
        "should do it — sound good?",
        f"I'd suggest {vehicle_text} and {helper_text} for this — does that work?",
    ]
    return _pick(variants, state)


# --------------------------------------------------------------------------
# Putting a whole turn's response together
# --------------------------------------------------------------------------


def compose_turn_response(patches: list[Patch], state: BookingState, decision: SlotDecision) -> str:
    """The full spoken response for one turn: acknowledgment plus the next
    question. `decision` must not be None -- see compose_question."""
    ack = compose_acknowledgment(patches, state)
    question = compose_question(decision, state)
    return f"{ack} {question}" if ack else question


# A single fixed line, not a rotated set like _QUESTIONS above: a greeting is
# heard at most once per session, so the repetition that _QUESTIONS's
# rotation exists to avoid can never happen here -- there is nothing to vary
# it against within one conversation. Spoken before anything is known, so it
# also cannot depend on state the way every other composed line here does.
GREETING = "Hi! I can help you book a move. Where are you moving from, and what are you sending?"
