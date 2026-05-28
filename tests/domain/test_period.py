from datetime import date

import pytest

from flexi.domain.period import Granularity, Period

THURSDAY = date(2026, 6, 11)  # week 24, a Thursday


def week(anchor: date = THURSDAY) -> Period:
    return Period(Granularity.WEEK, anchor)


@pytest.mark.parametrize(
    ("granularity", "start", "end"),
    [
        (Granularity.DAY, date(2026, 6, 11), date(2026, 6, 11)),
        (Granularity.WEEK, date(2026, 6, 8), date(2026, 6, 14)),
        (Granularity.MONTH, date(2026, 6, 1), date(2026, 6, 30)),
        (Granularity.YEAR, date(2026, 1, 1), date(2026, 12, 31)),
    ],
)
def test_span(granularity: Granularity, start: date, end: date) -> None:
    """It bounds each granularity around the anchor."""
    period = Period(granularity, THURSDAY)
    assert (period.start, period.end) == (start, end)


def test_len_matches_the_span() -> None:
    """It counts the dates it will iterate."""
    assert len(week()) == 7
    assert len(list(week().days())) == 7
    assert len(Period(Granularity.MONTH, THURSDAY)) == 30


def test_leave_year_follows_its_start() -> None:
    """It runs a year from the configured anniversary, not from January."""
    period = Period(Granularity.YEAR, THURSDAY, year_start=(4, 6))
    assert period.start == date(2026, 4, 6)
    assert period.end == date(2027, 4, 5)
    assert period.label == "2026/27"


def test_a_date_before_the_anniversary_is_in_the_previous_leave_year() -> None:
    """It does not roll the leave year over until the anniversary."""
    period = Period(Granularity.YEAR, date(2026, 3, 1), year_start=(4, 6))
    assert period.start == date(2025, 4, 6)
    assert period.label == "2025/26"


def test_zoom_keeps_the_anchor() -> None:
    """Zooming out and back in returns to the same week."""
    start = week()
    assert start.zoom(Granularity.MONTH).zoom(Granularity.WEEK) == start


@pytest.mark.parametrize(
    ("granularity", "anchor", "count", "expected"),
    [
        (Granularity.DAY, THURSDAY, 1, date(2026, 6, 12)),
        (Granularity.WEEK, THURSDAY, 1, date(2026, 6, 18)),
        (Granularity.WEEK, THURSDAY, -2, date(2026, 5, 28)),
        (Granularity.MONTH, date(2026, 1, 31), 1, date(2026, 2, 28)),
        (Granularity.MONTH, date(2026, 3, 31), -1, date(2026, 2, 28)),
        (Granularity.YEAR, date(2024, 2, 29), 1, date(2025, 2, 28)),
    ],
)
def test_shift(
    granularity: Granularity, anchor: date, count: int, expected: date
) -> None:
    """It clamps the anchor into a shorter month rather than raising."""
    assert Period(granularity, anchor).shift(count).anchor == expected


def test_shift_forward_reaches_the_future() -> None:
    """It can express next month, which an offset-from-today model cannot."""
    ahead = Period(Granularity.MONTH, THURSDAY).shift(3)
    assert ahead.start == date(2026, 9, 1)
    assert not ahead.is_current(THURSDAY)


def test_a_week_spanning_a_year_end() -> None:
    """It bounds a week that crosses into January."""
    period = Period(Granularity.WEEK, date(2026, 12, 31))
    assert (period.start, period.end) == (date(2026, 12, 28), date(2027, 1, 3))


@pytest.mark.parametrize(
    ("granularity", "label"),
    [
        (Granularity.DAY, "Thu 11 Jun 2026"),
        (Granularity.WEEK, "Week of 8 Jun"),
        (Granularity.MONTH, "June 2026"),
        (Granularity.YEAR, "2026"),
    ],
)
def test_label(granularity: Granularity, label: str) -> None:
    """It names itself the way a border title should read."""
    assert Period(granularity, THURSDAY).label == label


def test_contains_and_is_current() -> None:
    """It knows which dates it covers."""
    period = week()
    assert period.contains(date(2026, 6, 8))
    assert period.contains(date(2026, 6, 14))
    assert not period.contains(date(2026, 6, 15))
    assert period.is_current(THURSDAY)


def test_granularity_cycles() -> None:
    """It cycles day to week to month to year and back."""
    assert Granularity.DAY.next() is Granularity.WEEK
    assert Granularity.YEAR.next() is Granularity.DAY
    assert Granularity.DAY.previous() is Granularity.YEAR


def test_first_weekday_moves_the_week_boundary() -> None:
    """It starts the week on the configured day."""
    sunday_first = Period(Granularity.WEEK, THURSDAY, first_weekday=6)
    assert sunday_first.start == date(2026, 6, 7)
    assert sunday_first.end == date(2026, 6, 13)
