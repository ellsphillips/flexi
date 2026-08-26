"""When a leave year starts, and which one a date falls in.

Pure date arithmetic, which is the one thing this package exists for -- and it
was implemented four times. `Period._year_start` clamped a short month;
`AbsenceService.leave_year_bounds` had an ad-hoc guard for 29 February;
`LedgerService.balance` recomputed the start with no guard at all; and
`SettingsService.active_leave_year` built `date(ref.year, month, day)` directly,
which raises for a 29 February leave year in any of the three years out of four
that has no 29 February:

    active_leave_year(2027-06-01) -> ValueError: day is out of range for month

Callers then reached for whichever of the four they could get to, so the same
question was answered by different code depending on which service was nearest.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta


def clamp(year: int, month: int, day: int) -> date:
    """That day of that month, or the month's last day if it is shorter.

    A leave year starting on the 29th of February starts on the 28th in the
    three years out of four that do not have one. Anything else is a crash on a
    date somebody was entitled to choose.
    """
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def start_of(ref: date, month: int, day: int) -> date:
    """The first day of the leave year containing ``ref``."""
    this_year = clamp(ref.year, month, day)
    return this_year if ref >= this_year else clamp(ref.year - 1, month, day)


def active_year(ref: date, month: int, day: int) -> int:
    """The calendar year the leave year containing ``ref`` is filed under.

    An allowance belongs to a leave year, not a calendar year. Setting Flexi up
    in February against an April leave year files it under the year that has
    not started yet, and the allowance then cannot be found.
    """
    return start_of(ref, month, day).year


def bounds(ref: date, month: int, day: int) -> tuple[date, date]:
    """The first and last date of the leave year containing ``ref``, inclusive."""
    start = start_of(ref, month, day)
    following = clamp(start.year + 1, month, day)
    return start, following - timedelta(days=1)


def step(ref: date, month: int, day: int, count: int) -> date:
    """The same distance into the leave year ``count`` years away.

    Not ``ref`` plus twelve months. A leave year starting on 29 February starts
    on the 28th in a common year, and stepping the *anchor* twelve months from
    there lands on 28 February of a leap year -- which falls before that year's
    start and so resolves back to the year it came from. Paging forward did
    nothing at all, and the year beginning on the 29th could not be reached:

        Period(YEAR, 2031-02-28, year_start=(2, 29)).shift(1)  ->  the same year
        Period(YEAR, 2020-02-28, year_start=(2, 29)).shift(1)  ->  2021, not 2020

    So the step is taken between leave-year *starts*, which `clamp` already
    knows how to find, and the offset within the year is carried across and
    held inside it -- consecutive leave years differ in length by a day.
    """
    start = start_of(ref, month, day)
    first = clamp(start.year + count, month, day)
    last = bounds(first, month, day)[1]

    # The same date a year on, where that date is still inside the year being
    # moved to: `y` then a page then `m` should land on the month it left.
    same_date = clamp(ref.year + count, ref.month, ref.day)
    if first <= same_date <= last:
        return same_date

    # It is not, which happens only around a leave year that begins on 29
    # February. Keep the distance into the year instead, held inside it --
    # consecutive leave years differ in length by a day.
    return min(first + (ref - start), last)


def fraction_elapsed(start: date, end: date, today: date) -> float:
    """How far through a span today is, clamped to 0..1.

    Clamped rather than allowed to run past 1.0 so a pace marker can never
    leave the track -- a marker off the end of a gauge reads as a rendering
    fault, and the honest statement at that point is "all of it".

    Examples:
        >>> fraction_elapsed(date(2026, 1, 1), date(2026, 12, 31), date(2026, 7, 2))
        0.5
        >>> fraction_elapsed(date(2026, 1, 1), date(2026, 12, 31), date(2025, 6, 1))
        0.0
        >>> fraction_elapsed(date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 1))
        1.0
    """
    span = (end - start).days
    if span <= 0:
        return 1.0
    return min(1.0, max(0.0, (today - start).days / span))
