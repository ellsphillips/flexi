"""Which leave year a date falls in, and where it starts and ends."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

import pytest
from hypothesis import given

from flexi.domain import leaveyear
from flexi.domain.leaveyear import active_year, bounds, clamp, start_of
from tests import strategies

APRIL = (4, 6)
"""The sixth of April, which is the common UK leave year."""

LEAP_DAY = (2, 29)
"""A leave year start the settings form accepts."""


# ---- the ordinary case ----


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (date(2026, 4, 6), date(2026, 4, 6)),
        (date(2026, 4, 5), date(2025, 4, 6)),
        (date(2026, 12, 31), date(2026, 4, 6)),
        (date(2026, 1, 1), date(2025, 4, 6)),
    ],
    ids=["the first day", "the day before", "late in it", "early in the next"],
)
def test_start_of_is_the_most_recent_start(ref: date, expected: date) -> None:
    assert start_of(ref, *APRIL) == expected


def test_a_year_is_filed_under_its_start_year() -> None:
    """An allowance belongs to a leave year, not a calendar year."""
    assert active_year(date(2026, 2, 15), *APRIL) == 2025
    assert active_year(date(2026, 4, 6), *APRIL) == 2026


def test_a_year_ends_the_day_before_the_next() -> None:
    assert bounds(date(2026, 6, 1), *APRIL) == (date(2026, 4, 6), date(2027, 4, 5))


def test_bounds_have_no_gap_or_overlap() -> None:
    first = bounds(date(2026, 6, 1), *APRIL)
    second = bounds(date(2027, 6, 1), *APRIL)
    assert first[1].toordinal() + 1 == second[0].toordinal()


# ---- the twenty-ninth of February ----


def test_a_short_month_takes_its_last_day() -> None:
    assert clamp(2027, 2, 29) == date(2027, 2, 28)
    assert clamp(2028, 2, 29) == date(2028, 2, 29)
    assert clamp(2026, 4, 31) == date(2026, 4, 30)


def test_a_leap_day_start_survives_a_common_year() -> None:
    """`date(2027, 2, 29)` is a `ValueError`."""
    assert active_year(date(2027, 6, 1), *LEAP_DAY) == 2027
    assert start_of(date(2027, 6, 1), *LEAP_DAY) == date(2027, 2, 28)


def test_leap_day_years_stay_contiguous() -> None:
    common = bounds(date(2027, 6, 1), *LEAP_DAY)
    leap = bounds(date(2028, 6, 1), *LEAP_DAY)

    assert common == (date(2027, 2, 28), date(2028, 2, 28))
    assert leap == (date(2028, 2, 29), date(2029, 2, 27))
    assert common[1].toordinal() + 1 == leap[0].toordinal()


@pytest.mark.parametrize("year", [2026, 2027, 2028, 2029, 2030])
def test_every_year_has_a_start_whatever_the_setting(year: int) -> None:
    for month in range(1, 13):
        for day in (1, 28, 29, 30, 31):
            found = start_of(date(year, 6, 15), month, day)
            assert found.month == month
            assert found.day <= day


# Properties: the same statements over every date, not the named ones above.


@given(dates=strategies.dates, start=strategies.year_starts())
def test_a_clamped_start_is_always_a_real_date(
    dates: date, start: tuple[int, int]
) -> None:
    """`clamp` exists so no choice of leave year can raise."""
    month, day = start
    clamped = leaveyear.clamp(dates.year, month, day)
    assert clamped.month == month
    assert clamped.day == min(day, calendar.monthrange(dates.year, month)[1])


@given(ref=strategies.dates, start=strategies.year_starts())
def test_a_year_starts_on_or_before_its_dates(
    ref: date, start: tuple[int, int]
) -> None:
    month, day = start
    assert leaveyear.start_of(ref, month, day) <= ref


@given(ref=strategies.dates, start=strategies.year_starts())
def test_bounds_contain_the_date_and_agree(ref: date, start: tuple[int, int]) -> None:
    month, day = start
    first, last = leaveyear.bounds(ref, month, day)
    assert first <= ref <= last
    assert first == leaveyear.start_of(ref, month, day)
    assert leaveyear.active_year(ref, month, day) == first.year


@given(ref=strategies.dates, start=strategies.year_starts())
def test_every_date_in_a_year_reports_that_year(
    ref: date, start: tuple[int, int]
) -> None:
    """An allowance filed under one year has to be readable from every date in it."""
    month, day = start
    first, last = leaveyear.bounds(ref, month, day)
    for inside in (first, first + (last - first) // 2, last):
        assert leaveyear.bounds(inside, month, day) == (first, last)


@given(ref=strategies.dates, start=strategies.year_starts())
def test_leave_years_tile_the_calendar(ref: date, start: tuple[int, int]) -> None:
    month, day = start
    _first, last = leaveyear.bounds(ref, month, day)
    following = last + timedelta(days=1)
    assert leaveyear.start_of(following, month, day) == following
