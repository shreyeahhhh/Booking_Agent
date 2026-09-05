"""Turning natural-language time and quantity phrases into concrete values.

This is the module that exists so the extractor never has to: docs/design.md's
system prompt explicitly forbids the model from resolving dates or times
itself (Rule 3) -- it emits the raw phrase and this module does the arithmetic.
Getting "Saturday" wrong when today already is Saturday is exactly the kind of
mistake calendar-adjacent LLM output is prone to, and it is a real booking
error, not a cosmetic one.

Everything here returns None on a phrase it cannot confidently resolve. None
is not a failure -- the caller (domain.reducer) treats it as a signal to mark
the field AMBIGUOUS and ask again, which is the safe default for a system that
must never guess at something as consequential as a pickup date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.domain.state import TimeWindow

# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_ORDINAL_DAY = re.compile(r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)?\b")
_WEEKDAY_PHRASE = re.compile(r"^(?:(this|next|coming)\s+)?(\w+)$")


def resolve_relative_date(phrase: str, reference: datetime) -> date | None:
    """Resolve a natural date phrase relative to `reference` ("now").

    "Saturday" said on a Saturday resolves to the *coming* Saturday, one week
    out, not today -- see docs/test-plan.md's date-normalisation table. The
    same rule applies uniformly to a bare weekday, "this <weekday>" and
    "next <weekday>": each means the nearest occurrence of that weekday,
    rolling to the following week if today already is that weekday. English
    genuinely disagrees on what "next <weekday>" means when today is not
    already that weekday (this week's or the following week's) -- there is no
    signal in a bare phrase to disambiguate that, so this project does not
    try; it resolves to the nearer occurrence, the more common reading.
    """
    text = phrase.strip().lower()
    today = reference.date()

    if text in ("today", "asap", "now", "tonight"):
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text in ("day after tomorrow", "the day after tomorrow"):
        return today + timedelta(days=2)

    match = _WEEKDAY_PHRASE.match(text)
    if match:
        _modifier, word = match.groups()
        target = _WEEKDAYS.get(word)
        if target is not None:
            delta = (target - today.weekday()) % 7
            if delta == 0:
                delta = 7
            return today + timedelta(days=delta)

    ordinal = _ORDINAL_DAY.search(text)
    if ordinal:
        day = int(ordinal.group(1))
        try:
            candidate = date(reference.year, reference.month, day)
        except ValueError:
            # Not a valid day for *this* month (e.g. "the 30th" in February).
            # Deliberately not rolled forward to a month that would fit --
            # guessing which future month the caller meant risks a real
            # scheduling mistake, so this is surfaced as unresolved instead.
            return None
        if candidate < today:
            try:
                next_month = reference.month % 12 + 1
                next_year = reference.year + (1 if reference.month == 12 else 0)
                candidate = date(next_year, next_month, day)
            except ValueError:
                return None
        return candidate

    return None


# --------------------------------------------------------------------------
# Time of day
# --------------------------------------------------------------------------

# Bands tile the day exactly (06/12/16/20/24), matching TimeWindow's docstring
# in domain/state.py. Kept here, next to the parsing logic that produces exact
# times, so the two stay consistent by construction. Public (not _-prefixed):
# conversation/summary.py reuses it to display an hour range ("evening
# (4-8pm)") rather than hand-typing the same four boundaries a second time.
WINDOW_START_HOUR = {
    TimeWindow.MORNING: 6,
    TimeWindow.AFTERNOON: 12,
    TimeWindow.EVENING: 16,
    TimeWindow.NIGHT: 20,
}

_WINDOW_KEYWORDS = {
    "morning": TimeWindow.MORNING,
    "afternoon": TimeWindow.AFTERNOON,
    "evening": TimeWindow.EVENING,
    "night": TimeWindow.NIGHT,
    "tonight": TimeWindow.NIGHT,
}

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

_HHMM = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b")
_H_AMPM = re.compile(r"\b(\d{1,2})\s*(am|pm)\b")
_HALF_QUARTER = re.compile(
    r"\b(half|quarter)\s+(past|to)\s+(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\b"
)


def window_for_hour(hour: int) -> TimeWindow:
    """Which TimeWindow band a 24-hour clock hour falls into."""
    if hour < WINDOW_START_HOUR[TimeWindow.MORNING]:
        return TimeWindow.NIGHT  # e.g. 01:00 is still "tonight"'s tail end
    if hour < WINDOW_START_HOUR[TimeWindow.AFTERNOON]:
        return TimeWindow.MORNING
    if hour < WINDOW_START_HOUR[TimeWindow.EVENING]:
        return TimeWindow.AFTERNOON
    if hour < WINDOW_START_HOUR[TimeWindow.NIGHT]:
        return TimeWindow.EVENING
    return TimeWindow.NIGHT


@dataclass(frozen=True)
class TimeResolution:
    time_window: TimeWindow | None = None
    exact_time: str | None = None  # "HH:MM", 24-hour


def _to_24h(hour: int, minute: int, meridiem: str | None) -> tuple[int, int]:
    hour = hour % 12
    if meridiem == "pm":
        hour += 12
    elif meridiem is None and 1 <= hour <= 6:
        # No am/pm given and the hour is genuinely ambiguous (1-6). Moving
        # bookings skew daytime/evening, so an unmarked "four" is read as
        # 4pm, not 4am. A stated am/pm always wins over this default.
        hour += 12
    return hour, minute


def resolve_time_of_day(phrase: str) -> TimeResolution:
    """Resolve a time-of-day phrase to a window and, where possible, an exact time."""
    text = phrase.strip().lower()

    for word, window in _WINDOW_KEYWORDS.items():
        if word in text:
            return TimeResolution(time_window=window)

    match = _HHMM.search(text)
    if match:
        hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
        hour, minute = _to_24h(hour, minute, meridiem)
        return TimeResolution(window_for_hour(hour), f"{hour:02d}:{minute:02d}")

    match = _H_AMPM.search(text)
    if match:
        hour, meridiem = int(match.group(1)), match.group(2)
        hour, minute = _to_24h(hour, 0, meridiem)
        return TimeResolution(window_for_hour(hour), f"{hour:02d}:{minute:02d}")

    match = _HALF_QUARTER.search(text)
    if match:
        unit, direction, hour_word = match.groups()
        base_hour = int(hour_word) if hour_word.isdigit() else _NUMBER_WORDS[hour_word]
        minute = 30 if unit == "half" else 15
        if direction == "to":
            base_hour -= 1
            minute = 60 - minute
        hour, minute = _to_24h(base_hour, minute, None)
        return TimeResolution(window_for_hour(hour), f"{hour:02d}:{minute:02d}")

    return TimeResolution()


# --------------------------------------------------------------------------
# Quantities
# --------------------------------------------------------------------------

# Only words with one universally understood exact meaning. Deliberately
# excludes "a few" / "some" / "several" -- those are genuinely fuzzy, and
# guessing a specific number for them is exactly the "treating ambiguity as
# certainty" failure mode this project is designed against. A fuzzy quantity
# should stay AMBIGUOUS and be asked about, not silently resolved.
_QUANTITY_WORDS = {
    "a couple": 2,
    "a couple of": 2,
    "a pair": 2,
    "a pair of": 2,
    "half a dozen": 6,
    "a dozen": 12,
}


def resolve_quantity_word(phrase: str) -> int | None:
    text = phrase.strip().lower()
    # Longest phrase first so "half a dozen" matches before a shorter overlap could.
    for words in sorted(_QUANTITY_WORDS, key=len, reverse=True):
        if words in text:
            return _QUANTITY_WORDS[words]
    digits = re.search(r"\b(\d+)\b", text)
    if digits:
        return int(digits.group(1))
    for word, number in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return number
    return None
