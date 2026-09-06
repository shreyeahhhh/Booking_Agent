"""Date, time and quantity normalisation -- MASTER_PLAN.md step 1.3.

Cases mirror docs/test-plan.md's date-normalisation table exactly. That table
is the spec; these assertions are what make it enforceable.
"""

from datetime import date, datetime

import pytest

from app.domain.normalizers import (
    resolve_quantity_word,
    resolve_relative_date,
    resolve_time_of_day,
)
from app.domain.state import TimeWindow

FRI_11_SEP = datetime(2026, 9, 11, 10, 0)  # a real Friday
SAT_12_SEP = datetime(2026, 9, 12, 10, 0)  # a real Saturday


@pytest.mark.parametrize(
    "phrase,reference,expected",
    [
        ("tomorrow", FRI_11_SEP, date(2026, 9, 12)),
        ("this Saturday", FRI_11_SEP, date(2026, 9, 12)),
        ("Saturday", SAT_12_SEP, date(2026, 9, 19)),  # coming Saturday, not today
        ("next Friday", FRI_11_SEP, date(2026, 9, 18)),
        ("day after tomorrow", FRI_11_SEP, date(2026, 9, 13)),
        ("today", FRI_11_SEP, date(2026, 9, 11)),
    ],
)
def test_relative_date_resolution(phrase, reference, expected):
    assert resolve_relative_date(phrase, reference) == expected


def test_tonight_resolves_to_todays_date():
    reference = datetime(2026, 9, 11, 22, 0)
    assert resolve_relative_date("tonight", reference) == date(2026, 9, 11)


def test_bare_weekday_named_today_rolls_to_next_week_not_today():
    """The one edge case everything else here exists to get right."""
    assert resolve_relative_date("saturday", SAT_12_SEP) != SAT_12_SEP.date()
    assert resolve_relative_date("saturday", SAT_12_SEP) == date(2026, 9, 19)


def test_invalid_day_for_month_is_unresolved_not_guessed():
    """ "the 30th" in February must not silently roll to a month that has one."""
    assert resolve_relative_date("the 30th", datetime(2026, 2, 10)) is None


def test_unparseable_phrase_returns_none():
    assert resolve_relative_date("sometime next month probably", FRI_11_SEP) is None


# --- calendar-date fallback (dateutil) -----------------------------------
# A real, live-observed crash: "September 7, Monday" matched none of the
# patterns above, so its raw phrase ended up stored verbatim as
# schedule.date's value and later crashed templates.format_date_short's
# date.fromisoformat call. These phrasings are all real ways a user might
# state the same date the crash was observed on.


@pytest.mark.parametrize(
    "phrase",
    [
        "September 7, Monday",  # the exact phrase observed live
        "Monday, September 7",
        "September 7th",
        "the 7th of September",
        "7 September",
        "on September 7th",
        "next Monday, September 7",
    ],
)
def test_calendar_date_phrases_resolve_via_the_dateutil_fallback(phrase):
    assert resolve_relative_date(phrase, datetime(2026, 9, 5)) == date(2026, 9, 7)


def test_a_bare_month_and_day_already_past_this_year_rolls_to_next_year():
    """Said in October, "September 7" must mean next September, not the one
    that already passed -- the same always-resolve-forward rule the
    weekday and ordinal-day branches already apply."""
    reference = datetime(2026, 10, 15)
    assert resolve_relative_date("September 7", reference) == date(2027, 9, 7)


def test_clearly_non_date_phrases_are_not_misread_as_dates_by_the_fallback():
    """fuzzy=True must not turn "no date here at all" into a guessed one --
    verified live before trusting it (see normalizers.py's own docstring),
    re-asserted here as a permanent regression test."""
    for phrase in ["a sofa and two cupboards", "third floor", "yes there is a lift"]:
        assert resolve_relative_date(phrase, FRI_11_SEP) is None


def test_time_window_keywords():
    assert resolve_time_of_day("morning").time_window == TimeWindow.MORNING
    assert resolve_time_of_day("in the afternoon").time_window == TimeWindow.AFTERNOON
    assert resolve_time_of_day("this evening").time_window == TimeWindow.EVENING
    assert resolve_time_of_day("tonight").time_window == TimeWindow.NIGHT


def test_half_past_four_resolves_to_1630_evening():
    result = resolve_time_of_day("half past four")
    assert result.exact_time == "16:30"
    assert result.time_window == TimeWindow.EVENING


def test_explicit_am_pm_overrides_the_ambiguous_hour_default():
    assert resolve_time_of_day("4am").exact_time == "04:00"
    assert resolve_time_of_day("4pm").exact_time == "16:00"


def test_quarter_to_five():
    result = resolve_time_of_day("quarter to five")
    assert result.exact_time == "16:45"


def test_vague_time_phrase_is_unresolved():
    result = resolve_time_of_day("sometime later")
    assert result.time_window is None
    assert result.exact_time is None


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("a couple of boxes", 2),
        ("a pair of chairs", 2),
        ("a dozen chairs", 12),
        ("half a dozen boxes", 6),
        ("three cupboards", 3),
        ("5 chairs", 5),
    ],
)
def test_quantity_word_resolution(phrase, expected):
    assert resolve_quantity_word(phrase) == expected


def test_genuinely_fuzzy_quantities_stay_unresolved():
    """ "a few" / "some" must NOT be guessed at -- that is exactly the
    treating-ambiguity-as-certainty failure mode this project avoids."""
    assert resolve_quantity_word("a few boxes") is None
    assert resolve_quantity_word("some furniture") is None
