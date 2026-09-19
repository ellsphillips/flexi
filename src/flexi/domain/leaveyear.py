"""When a leave year starts, and which one a date falls in.

The single source of leave-year date arithmetic, for every service that needs
it. An anchor of 29 February needs clamping: `date(year, 2, 29)` raises in the
three years out of four that have no 29 February.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

__all__ = (
    "active_year",
    "bounds",
    "clamp",
    "fraction_elapsed",
    "start_of",
    "step",
)


def clamp(year: int, month: int, day: int) -> date:
    """Return that day of that month, or the month's last day if it is shorter.

    A leave year starting on 29 February starts on the 28th in the three years
    out of four that have no 29 February.
    """
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def start_of(ref: date, month: int, day: int) -> date:
    """The first day of the leave year containing ``ref``."""
    this_year = clamp(ref.year, month, day)
    return this_year if ref >= this_year else clamp(ref.year - 1, month, day)


def active_year(ref: date, month: int, day: int) -> int:
    """Return the calendar year the leave year containing ``ref`` is filed under.

    An allowance belongs to a leave year, not a calendar year: under an April
    leave year, a February date files under the previous calendar year.
    """
    return start_of(ref, month, day).year


def bounds(ref: date, month: int, day: int) -> tuple[date, date]:
    """The first and last date of the leave year containing ``ref``, inclusive."""
    start = start_of(ref, month, day)
    following = clamp(start.year + 1, month, day)
    return start, following - timedelta(days=1)


def step(ref: date, month: int, day: int, count: int) -> date:
    """Return the same distance into the leave year ``count`` years away.

    The step runs between leave-year *starts*, not from ``ref`` plus twelve
    months: an anchor of 29 February clamps to the 28th, and 28 February of a
    leap year falls before that year's start, resolving back into the year it
    came from. The offset is then held inside the target year.
    """
    start = start_of(ref, month, day)
    first = clamp(start.year + count, month, day)
    last = bounds(first, month, day)[1]

    # The same date a year on, when it still falls inside the target year:
    # `y`, a page, then `m` lands on the month it left.
    same_date = clamp(ref.year + count, ref.month, ref.day)
    if first <= same_date <= last:
        return same_date

    # Otherwise, which happens only around a 29 February leave year, keep the
    # distance into the year, clamped: consecutive years differ by a day.
    return min(first + (ref - start), last)


def fraction_elapsed(start: date, end: date, today: date) -> float:
    """Return how far through a span today is, clamped to 0..1.

    The clamp keeps a pace marker on its track.

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
