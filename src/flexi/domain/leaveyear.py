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
