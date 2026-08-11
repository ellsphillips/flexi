import itertools
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from flexi.domain.period import Granularity, Period
from tests import strategies

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


# -- properties ------------------------------------------------------------
#
# A period is arithmetic on dates, and the failures that matter are the ones no
# hand-written example thinks to try: shifting a month from the 31st, a year
# starting on 29 February, a week whose first day is Sunday.


@st.composite
def periods(draw: st.DrawFn) -> Period:
    """Any period a user could put on screen."""
    return Period(
        draw(strategies.granularities),
        draw(strategies.dates),
        draw(strategies.year_starts()),
        draw(strategies.first_weekdays),
    )


@given(period=periods())
def test_a_period_contains_its_own_anchor(period: Period) -> None:
    """The date you are standing on is always inside the span you are looking at."""
    assert period.start <= period.anchor <= period.end
    assert period.contains(period.anchor)


@given(period=periods())
def test_the_length_is_the_number_of_days_it_yields(period: Period) -> None:
    days = list(period.days())
    assert len(period) == len(days)
    assert days[0] == period.start
    assert days[-1] == period.end
    assert all(
        later - earlier == timedelta(days=1)
        for earlier, later in itertools.pairwise(days)
    ), "the span is contiguous"


@given(period=periods())
def test_consecutive_periods_tile_without_gap_or_overlap(period: Period) -> None:
    """The day after this span ends is the first day of the next one.

    The property that makes paging trustworthy: a day cannot fall between two
    periods, and cannot appear in both. `_add_months` clamping the 31st is what
    makes this non-obvious for months and for a leave year starting late in one.
    """
    following = period.shift(1)
    assert following.start == period.end + timedelta(days=1)
    assert period.shift(-1).end == period.start - timedelta(days=1)


@given(period=periods(), moment=strategies.dates)
def test_going_to_a_date_puts_that_date_in_the_span(
    period: Period, moment: date
) -> None:
    assert period.go_to(moment).contains(moment)


@given(period=periods(), granularity=strategies.granularities)
def test_zooming_is_lossless(period: Period, granularity: Granularity) -> None:
    """`m` then `w` returns to the week you were standing on.

    Zoom moves the width and never the anchor, which is the whole reason a
    period is an anchor plus a granularity rather than a pair of dates.
    """
    assert period.zoom(granularity).zoom(period.granularity) == period


@given(period=periods(), count=st.integers(min_value=-24, max_value=24))
def test_shifting_back_and_forward_settles_after_one_clamp(
    period: Period, count: int
) -> None:
    """Stepping off the 31st is lossy exactly once, and never again.

    January the 31st shifted forward lands on the 28th of February and cannot
    find its way back to the 31st — that is the clamp doing its job. What must
    not happen is drift: shifting on from there has to be stable, or paging
    through a year would walk the anchor backwards a day at a time.
    """
    moved = period.shift(count)
    assert moved.shift(-count).shift(count) == moved
