"""Which leave year a date falls in, and where it starts and ends.

This was implemented four times: once correctly in `Period`, once with an ad-hoc
guard in `AbsenceService`, once with no guard in `LedgerService`, and once in
`SettingsService` in a way that raised outright. Callers reached for whichever
they could get to, so the same question had different answers depending on which
service happened to be nearest.
"""

from __future__ import annotations

from datetime import date

import pytest

from flexi.domain.leaveyear import active_year, bounds, clamp, start_of

APRIL = (4, 6)
"""The sixth of April, which is the common UK leave year."""

LEAP_DAY = (2, 29)
"""A leave year somebody is entitled to choose, and which used to crash."""


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
def test_a_date_belongs_to_the_year_that_started_before_it(
    ref: date, expected: date
) -> None:
    assert start_of(ref, *APRIL) == expected


def test_the_year_is_filed_under_the_year_it_started() -> None:
    """An allowance belongs to a leave year, not a calendar year.

    Setting Flexi up in February against an April leave year filed the
    allowance under a year that had not begun, and it could not be found again.
    """
    assert active_year(date(2026, 2, 15), *APRIL) == 2025
    assert active_year(date(2026, 4, 6), *APRIL) == 2026


def test_the_year_runs_to_the_day_before_the_next_one() -> None:
    assert bounds(date(2026, 6, 1), *APRIL) == (date(2026, 4, 6), date(2027, 4, 5))


def test_the_bounds_are_a_whole_year_with_no_gap_or_overlap() -> None:
    first = bounds(date(2026, 6, 1), *APRIL)
    second = bounds(date(2027, 6, 1), *APRIL)
    assert first[1].toordinal() + 1 == second[0].toordinal()


# ---- the twenty-ninth of February ----


def test_a_short_month_takes_its_last_day() -> None:
    assert clamp(2027, 2, 29) == date(2027, 2, 28)
    assert clamp(2028, 2, 29) == date(2028, 2, 29)
    assert clamp(2026, 4, 31) == date(2026, 4, 30)


def test_a_leap_day_leave_year_does_not_raise_in_a_common_year() -> None:
    """`date(2027, 2, 29)` is a ValueError, and that is what used to happen.

    Three years out of four, on a setting somebody was allowed to save.
    """
    assert active_year(date(2027, 6, 1), *LEAP_DAY) == 2027
    assert start_of(date(2027, 6, 1), *LEAP_DAY) == date(2027, 2, 28)


def test_a_leap_day_leave_year_is_still_contiguous() -> None:
    common = bounds(date(2027, 6, 1), *LEAP_DAY)
    leap = bounds(date(2028, 6, 1), *LEAP_DAY)

    assert common == (date(2027, 2, 28), date(2028, 2, 28))
    assert leap == (date(2028, 2, 29), date(2029, 2, 27))
    assert common[1].toordinal() + 1 == leap[0].toordinal()


@pytest.mark.parametrize("year", [2026, 2027, 2028, 2029, 2030])
def test_every_year_has_a_start_whatever_the_setting(year: int) -> None:
    """Every month and day the settings form will accept, for five years."""
    for month in range(1, 13):
        for day in (1, 28, 29, 30, 31):
            found = start_of(date(year, 6, 15), month, day)
            assert found.month == month
            assert found.day <= day
