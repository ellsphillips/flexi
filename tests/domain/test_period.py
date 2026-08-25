import itertools
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from flexi.constants import Granularity
from flexi.domain import leaveyear
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
    """It bounds each granularity around the anchor."""
    period = Period(granularity, THURSDAY)
    assert (period.start, period.end) == (start, end)


def test_a_period_asked_for_a_date_defaults_to_the_week_around_it() -> None:
    """Every screen opens on a week, and none of them says so.

    The dashboard, the leave screen and the fallback in a module that has not
    been told a period yet all call `containing` for today. A default of the day
    or the month would change what the application opens on without a single
    caller changing.
    """
    period = Period.containing(THURSDAY)

    assert period.granularity is Granularity.WEEK
    assert (period.start, period.end) == (date(2026, 6, 8), date(2026, 6, 14))


def test_the_settings_a_period_is_opened_with_are_carried_into_it() -> None:
    """A leave year and a first weekday are settings, not defaults.

    `containing` forwards them positionally into a four-field dataclass, so a
    field inserted between them would silently hand `year_start` to
    `first_weekday`. Neither mistake raises: the screen would simply draw
    January to January for somebody whose leave year runs from April, and put
    Monday at the top of a week they asked to start on Sunday.
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
    assert not ahead.contains(THURSDAY)


LEAP_START = (2, 29)
"""A leave year beginning on the 29th of February.

`leaveyear.clamp` exists because the settings screen lets somebody choose it,
and `Period.end` already carries a comment about the day it once lost. `shift`
was never given the same treatment.
"""


def test_paging_forward_from_a_leave_year_that_starts_on_29_february_moves_it() -> None:
    """`→` on the Leave screen has to show a different year afterwards.

    2031 has no 29 February, so the 2031/32 leave year starts on the 28th.
    Stepping the anchor twelve months lands on 28 February 2032 — which *is* a
    leap year, so the 28th now falls before that year's start and resolves back
    to the year it came from. The screen redraws with the same title, the same
    entitlement and the same bookings, and the key reads as broken.
    """
    stuck = Period(Granularity.YEAR, date(2031, 2, 28), year_start=LEAP_START)

    assert stuck.shift(1).start == stuck.end + timedelta(days=1)


def test_paging_forward_across_29_february_does_not_skip_a_leave_year() -> None:
    """The year between them is somebody's entitlement, and it is unreachable.

    From the leave year ending on 28 February 2020, one step forward should
    open the year beginning on the 29th. The clamped anchor lands a year beyond
    it instead, so 2020/21 cannot be paged to at all — and the days booked in it
    are on screen nowhere, while every service still counts them.
    """
    before = Period(Granularity.YEAR, date(2020, 2, 28), year_start=LEAP_START)

    assert before.shift(1).start == date(2020, 2, 29)


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


def test_contains() -> None:
    """It knows which dates it covers."""
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
def test_a_granularity_names_itself_apart_from_the_value_it_is_stored_as(
    granularity: Granularity, heading: str
) -> None:
    """The records table wants a title and the command palette wants a phrase.

    Both read this enum, and a `label` that handed back the raw value would put
    a lower-case "week" at the head of the table — while the palette, which
    lowers the label to read "Period: week", would be unaffected and so would
    not notice.
    """
    assert granularity.label == heading
    assert granularity.value == heading.lower()


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


@given(period=periods())
def test_a_year_period_is_the_leave_year_the_services_use(period: Period) -> None:
    """One question, one answer, whichever surface is asking.

    `Period.end` derived the next start from *this* start, which clamps twice: a
    leave year beginning on 29 February starts on the 28th in a common year, and
    carrying that 28th forward ended the year on 28 February instead of 29. The
    Leave screen therefore drew a year one day shorter than every service
    counted, and 28 February 2020 belonged to neither year on screen.
    """
    if period.granularity is not Granularity.YEAR:
        return
    assert (period.start, period.end) == leaveyear.bounds(
        period.anchor, *period.year_start
    )


@given(granularity=strategies.granularities)
def test_the_two_directions_of_the_cycle_are_opposites(
    granularity: Granularity,
) -> None:
    """`p` forward then back is where you started, and forward is not back.

    Nothing distinguished `previous` from `next`: changing the minus to a plus
    left the whole suite green, because every test that walked the cycle walked
    it in one direction.
    """
    assert granularity.next().previous() == granularity
    assert granularity.previous().next() == granularity
    assert granularity.next() != granularity.previous()
    assert granularity.next().next() == granularity.previous().previous(), (
        "four granularities, so two steps either way meet in the middle"
    )


@given(period=periods(), count=st.integers(min_value=-8, max_value=8))
def test_paging_lands_on_a_different_span_every_time(
    period: Period, count: int
) -> None:
    """A key that redraws the same thing reads as a key that is broken.

    The `shift` counterpart to the tiling property: stepping by anything other
    than zero must move the span, at every granularity and every leave-year
    start. `Period.shift` for a year stepped the anchor twelve months, so from
    a leave year starting on a clamped 29 February it landed back inside the
    year it came from.
    """
    moved = period.shift(count)
    if count == 0:
        assert (moved.start, moved.end) == (period.start, period.end)
        return
    assert (moved.start, moved.end) != (period.start, period.end)
    assert (moved.start > period.end) if count > 0 else (moved.end < period.start)
