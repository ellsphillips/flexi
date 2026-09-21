import itertools
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from flexi.constants import Granularity
from flexi.domain import leaveyear
from flexi.domain.dates import SUPPORTED_FIRST, SUPPORTED_LAST
from flexi.domain.period import Period
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
    """Bounds each granularity around the anchor."""
    period = Period(granularity, THURSDAY)
    assert (period.start, period.end) == (start, end)


def test_containing_defaults_to_the_week() -> None:
    """Every surface with no period of its own opens through `containing`."""
    period = Period.containing(THURSDAY)

    assert period.granularity is Granularity.WEEK
    assert (period.start, period.end) == (date(2026, 6, 8), date(2026, 6, 14))


def test_containing_carries_its_settings() -> None:
    """`containing` forwards its settings positionally into a four-field class.

    A field inserted between them hands `year_start` to `first_weekday`, and
    neither mistake raises.
    """
    period = Period.containing(
        date(2026, 3, 1),
        Granularity.YEAR,
        year_start=(4, 6),
        first_weekday=6,
    )

    assert period.contains(date(2026, 3, 1))
    assert period.label == "2025/26"
    assert period.zoom(Granularity.WEEK).start.weekday() == 6


def test_len_matches_the_span() -> None:
    assert len(week()) == 7
    assert len(list(week().days())) == 7
    assert len(Period(Granularity.MONTH, THURSDAY)) == 30


def test_leave_year_follows_its_start() -> None:
    period = Period(Granularity.YEAR, THURSDAY, year_start=(4, 6))
    assert period.start == date(2026, 4, 6)
    assert period.end == date(2027, 4, 5)
    assert period.label == "2026/27"


def test_date_before_the_anniversary_is_last_year() -> None:
    period = Period(Granularity.YEAR, date(2026, 3, 1), year_start=(4, 6))
    assert period.start == date(2025, 4, 6)
    assert period.label == "2025/26"


def test_zoom_keeps_the_anchor() -> None:
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
    """Clamps the anchor into a shorter month."""
    assert Period(granularity, anchor).shift(count).anchor == expected


def test_shift_forward_reaches_the_future() -> None:
    ahead = Period(Granularity.MONTH, THURSDAY).shift(3)
    assert ahead.start == date(2026, 9, 1)
    assert not ahead.contains(THURSDAY)


LEAP_START = (2, 29)
"""A leave year beginning on the 29th of February, which settings allow."""


def test_paging_from_a_29_february_year_moves() -> None:
    """2031 has no 29 February, so the 2031/32 leave year starts on the 28th.

    Twelve months on is 28 February 2032, a leap year, where that date falls
    before the year's start and resolves back into the year it came from.
    """
    stuck = Period(Granularity.YEAR, date(2031, 2, 28), year_start=LEAP_START)

    assert stuck.shift(1).start == stuck.end + timedelta(days=1)


def test_paging_across_29_february_skips_no_year() -> None:
    """One step on from the year ending 28 February 2020 opens 2020/21."""
    before = Period(Granularity.YEAR, date(2020, 2, 28), year_start=LEAP_START)

    assert before.shift(1).start == date(2020, 2, 29)


def test_week_spanning_a_year_end() -> None:
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
    assert Period(granularity, THURSDAY).label == label


def test_contains() -> None:
    period = week()
    assert period.contains(date(2026, 6, 8))
    assert period.contains(date(2026, 6, 14))
    assert not period.contains(date(2026, 6, 15))
    assert period.contains(THURSDAY)


@pytest.mark.parametrize(
    ("granularity", "heading"),
    [
        (Granularity.DAY, "Day"),
        (Granularity.WEEK, "Week"),
        (Granularity.MONTH, "Month"),
        (Granularity.YEAR, "Year"),
    ],
)
def test_granularity_label_differs_from_its_value(
    granularity: Granularity, heading: str
) -> None:
    """The records table heads a column with the label; the palette lowers it."""
    assert granularity.label == heading
    assert granularity.value == heading.lower()


def test_granularity_cycles() -> None:
    assert Granularity.DAY.next() is Granularity.WEEK
    assert Granularity.YEAR.next() is Granularity.DAY, "and wraps"


def test_first_weekday_moves_the_week_boundary() -> None:
    sunday_first = Period(Granularity.WEEK, THURSDAY, first_weekday=6)
    assert sunday_first.start == date(2026, 6, 7)
    assert sunday_first.end == date(2026, 6, 13)


# ---- properties ----
#
# A period is arithmetic on dates, and the failures that matter are the ones no
# hand-written example thinks to try: shifting a month from the 31st, a year
# starting on 29 February, a week whose first day is Sunday.


@st.composite
def periods(draw: st.DrawFn, granularity: Granularity | None = None) -> Period:
    """Draw any period a user could put on screen, or one of a granularity."""
    return Period(
        granularity if granularity is not None else draw(strategies.granularities),
        draw(strategies.dates),
        draw(strategies.year_starts()),
        draw(strategies.first_weekdays),
    )


@given(period=periods())
def test_period_contains_its_own_anchor(period: Period) -> None:
    assert period.start <= period.anchor <= period.end
    assert period.contains(period.anchor)


@given(period=periods())
def test_length_matches_the_days_yielded(period: Period) -> None:
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
    """`_add_months` clamps the 31st, which is what makes months hard."""
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
    """Zoom moves the width and never the anchor."""
    assert period.zoom(granularity).zoom(period.granularity) == period


@given(period=periods(), count=st.integers(min_value=-24, max_value=24))
def test_shifting_settles_after_one_clamp(period: Period, count: int) -> None:
    """Stepping off the 31st is lossy once; shifting on from there is stable."""
    moved = period.shift(count)
    assert moved.shift(-count).shift(count) == moved


@given(period=periods(granularity=Granularity.YEAR))
def test_year_period_matches_the_leave_year(period: Period) -> None:
    """The screens and the services bound a leave year the same way."""
    assert (period.start, period.end) == leaveyear.bounds(
        period.anchor, *period.year_start
    )


@given(period=periods(), count=st.integers(min_value=-8, max_value=8))
def test_paging_lands_on_a_different_span_every_time(
    period: Period, count: int
) -> None:
    """Stepping by anything but zero moves the span, at every granularity."""
    moved = period.shift(count)
    if count == 0:
        assert (moved.start, moved.end) == (period.start, period.end)
        return
    assert (moved.start, moved.end) != (period.start, period.end)
    assert (moved.start > period.end) if count > 0 else (moved.end < period.start)


# ---- the end of the calendar ----


EDGES = [
    pytest.param(Granularity.DAY, SUPPORTED_LAST, (1, 1), 1, id="a day past the end"),
    pytest.param(Granularity.WEEK, SUPPORTED_LAST, (1, 1), 1, id="a week past the end"),
    pytest.param(
        Granularity.MONTH, SUPPORTED_LAST, (1, 1), 1, id="a month past the end"
    ),
    pytest.param(Granularity.YEAR, SUPPORTED_LAST, (1, 1), 1, id="a year past the end"),
    pytest.param(
        Granularity.DAY, SUPPORTED_FIRST, (1, 1), -1, id="a day before the start"
    ),
    pytest.param(
        Granularity.WEEK, SUPPORTED_FIRST, (1, 1), -1, id="a week before the start"
    ),
    pytest.param(
        Granularity.MONTH, SUPPORTED_FIRST, (1, 1), -1, id="a month before the start"
    ),
    pytest.param(
        Granularity.YEAR, SUPPORTED_FIRST, (4, 6), -1, id="a leave year before year one"
    ),
]


@pytest.mark.parametrize(("granularity", "anchor", "year_start", "count"), EDGES)
def test_paging_off_the_end_stays_put(
    granularity: Granularity, anchor: date, year_start: tuple[int, int], count: int
) -> None:
    """The supported window is a year short of `date`'s own at each end.

    A leave year reaches into the calendar year on either side of the date it
    holds. Paging is the one way to walk past the window: the parser refuses a
    date outside it.
    """
    period = Period(granularity, anchor, year_start)

    assert period.shift(count) == period


def test_paging_back_from_the_edge_still_works() -> None:
    period = Period(Granularity.YEAR, SUPPORTED_LAST)

    assert period.shift(-1).label == "9997"
