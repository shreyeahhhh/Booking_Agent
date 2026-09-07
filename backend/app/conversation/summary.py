"""The final booking summary -- MASTER_PLAN.md step 2.6.

Generated entirely from state, the same discipline as everything else in
conversation/: no LLM call, no free-form generation. What the user sees at
review time is a deterministic rendering of exactly what the reducer has
recorded, nothing the model phrased on its own.

Only the written form lives here. docs/design.md SS5.6 also describes a
condensed spoken form, chunked to Orpheus's 200-character-per-request limit
-- that depends on real TTS behaviour to get right and belongs with phase
3's voice work, not here. `build_summary`'s structured `SummaryLine` list is
the seam: a future spoken renderer consumes the same lines this module's
text renderer does, rather than re-deriving them from state a second time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.conversation.templates import format_date_full, format_item_list, format_vehicle
from app.domain.normalizers import WINDOW_START_HOUR
from app.domain.specs import ALL_SCALAR_PATHS, get_field, spec_for
from app.domain.state import Address, BookingState, FieldStatus, Schedule, TimeWindow


@dataclass(frozen=True)
class SummaryLine:
    label: str
    value: str


def _period(hour: int) -> str:
    return "am" if (hour % 24) < 12 else "pm"


def _hour12(hour: int) -> int:
    return (hour % 12) or 12


# Each window's end is the next window's start -- derived from
# WINDOW_START_HOUR, not a second hand-typed set of boundaries. NIGHT has no
# "next" window to borrow from, so its end (midnight) is the one fixed point.
_WINDOW_END_HOUR: dict[TimeWindow, int] = {
    TimeWindow.MORNING: WINDOW_START_HOUR[TimeWindow.AFTERNOON],
    TimeWindow.AFTERNOON: WINDOW_START_HOUR[TimeWindow.EVENING],
    TimeWindow.EVENING: WINDOW_START_HOUR[TimeWindow.NIGHT],
    TimeWindow.NIGHT: 24,
}


def _format_hour_range(window: TimeWindow) -> str:
    """Displays a window as an hour range, e.g. "evening" -> "4-8pm".

    "morning" -> "6am-12pm" crosses am/pm, so both ends need their own
    suffix, unlike a range that stays on one side.
    """
    start, end = WINDOW_START_HOUR[window], _WINDOW_END_HOUR[window]
    if _period(start) == _period(end):
        return f"{_hour12(start)}-{_hour12(end)}{_period(end)}"
    return f"{_hour12(start)}{_period(start)}-{_hour12(end)}{_period(end)}"


def _format_exact_time(hhmm: str) -> str:
    """Displays a stored "HH:MM" as e.g. "16:30" -> "4:30pm".

    Manual leading-zero strip, not a %-I strftime flag -- that flag is
    Linux/Mac-only and silently wrong on Windows, exactly the portability
    trap format_date_full already avoids the same way in templates.py.
    """
    parsed = datetime.strptime(hhmm, "%H:%M")
    label = parsed.strftime("%I:%M%p").lower()
    return label[1:] if label.startswith("0") else label


def _render_address(address: Address) -> str | None:
    parts: list[str] = []
    if address.locality.status != FieldStatus.EMPTY:
        parts.append(str(address.locality.value))
    if not parts:
        return None
    if address.floor.status != FieldStatus.EMPTY:
        floor = address.floor.value
        parts.append("ground floor" if floor == 0 else f"{floor}{_ordinal(floor)} floor")
    if address.has_lift.status != FieldStatus.EMPTY:
        parts.append("lift available" if address.has_lift.value else "no lift")
    if address.landmark.status != FieldStatus.EMPTY:
        parts.append(f"near {address.landmark.value}")
    return ", ".join(parts)


_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def _ordinal(n: int) -> str:
    return _ORDINAL_SUFFIX.get(n if n < 20 else n % 10, "th")


def _without_article(phrase: str) -> str:
    """format_vehicle returns "a Tata Ace" -- right for a sentence ("I'd
    suggest a Tata Ace..."), but a label-value table reads better as
    "Vehicle  Tata Ace", the same way "Pickup  Koramangala" carries no
    article. Strips it here rather than changing format_vehicle's contract,
    which templates.py's prose already depends on as-is."""
    for article in ("a ", "an "):
        if phrase.startswith(article):
            return phrase[len(article) :]
    return phrase


def _render_schedule(schedule: Schedule) -> str | None:
    if schedule.is_asap.status != FieldStatus.EMPTY and schedule.is_asap.value:
        return "as soon as possible"

    parts: list[str] = []
    if schedule.date.status != FieldStatus.EMPTY:
        parts.append(format_date_full(schedule.date.value))
    if schedule.exact_time.status != FieldStatus.EMPTY:
        parts.append(_format_exact_time(schedule.exact_time.value))
    elif schedule.time_window.status != FieldStatus.EMPTY:
        window = schedule.time_window.value
        parts.append(f"{window.value} ({_format_hour_range(window)})")
    return ", ".join(parts) if parts else None


def _render_corrections(state: BookingState) -> str | None:
    """Only fields that were actually corrected, not a status report on
    every field -- see docs/architecture.md's "Corrections": the point is
    a visible history, not a wall of unchanged values."""
    notes: list[str] = []
    for path in ALL_SCALAR_PATHS:
        field = get_field(state, path)
        if not field.revisions:
            continue
        try:
            label = spec_for(path).label
        except KeyError:
            label = path.rsplit(".", 1)[-1].replace("_", " ")
        previous = " then ".join(str(r.value) for r in field.revisions)
        notes.append(f"{label} (was {previous})")
    return "; ".join(notes) if notes else None


def build_summary(state: BookingState) -> list[SummaryLine]:
    """The structured summary: one line per populated section, in the same
    order the PDF brief's own suggested categories list them (pickup, drop,
    items, vehicle/service, date/time, additional requirements) -- with
    date/time folded in after items here since that reads more naturally as
    prose than it changes anything about what is covered."""
    lines: list[SummaryLine] = []

    pickup = _render_address(state.pickup)
    if pickup:
        lines.append(SummaryLine("Pickup", pickup))

    drop = _render_address(state.drop)
    if drop:
        lines.append(SummaryLine("Drop", drop))

    schedule = _render_schedule(state.schedule)
    if schedule:
        lines.append(SummaryLine("Date/time", schedule))

    if state.goods.items:
        lines.append(SummaryLine("Items", format_item_list(state.goods.items)))

    vehicle = get_field(state, "service.vehicle_type")
    if vehicle.status != FieldStatus.EMPTY:
        lines.append(SummaryLine("Vehicle", _without_article(format_vehicle(vehicle.value))))

    helpers = get_field(state, "service.helpers_required")
    if helpers.status != FieldStatus.EMPTY:
        lines.append(SummaryLine("Helpers", str(helpers.value)))

    extras: list[str] = []
    packing = get_field(state, "service.needs_packing")
    if packing.status != FieldStatus.EMPTY and packing.value:
        extras.append("packing help")
    disassembly = get_field(state, "service.needs_disassembly")
    if disassembly.status != FieldStatus.EMPTY and disassembly.value:
        extras.append("disassembly")
    extras.extend(note.text for note in state.notes)
    if extras:
        lines.append(SummaryLine("Notes", "; ".join(extras)))

    corrections = _render_corrections(state)
    if corrections:
        lines.append(SummaryLine("Corrected", corrections))

    if state.assumptions:
        lines.append(SummaryLine("Assumed", "; ".join(a.note for a in state.assumptions)))

    return lines


def format_summary_text(lines: list[SummaryLine]) -> str:
    if not lines:
        return "Nothing has been recorded yet."
    width = max(len(line.label) for line in lines)
    return "\n".join(f"{line.label:<{width}}  {line.value}" for line in lines)


def render_summary(state: BookingState) -> str:
    return format_summary_text(build_summary(state))
