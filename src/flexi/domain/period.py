"""Which span of dates is on screen.

A period is an anchor plus a granularity, not an offset from today: an offset
cannot express next month, and Flexi books leave in the future.

Zooming keeps the anchor, so ``m`` then ``w`` returns to the week the cursor
was standing on. Going to today resets the anchor and not the granularity.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta

from flexi.constants import Granularity
from flexi.domain import leaveyear
from flexi.domain.dates import (
    SUPPORTED_FIRST,
    SUPPORTED_LAST,
    add_months,
    days_between,
    week_start,
)
from flexi.domain.format import day_month, long_date, month_title

__all__ = ("Period",)


@dataclass(frozen=True, slots=True)
class Period:
    """A span of dates, identified by any date inside it.

    Operations move or reinterpret ``anchor``, which is what keeps zooming
    lossless. ``year_start`` affects only :attr:`Granularity.YEAR`,
    ``first_weekday`` only :attr:`Granularity.WEEK`.

    Each ``match self.granularity`` below ends on ``case Granularity.YEAR``
    carrying ``# pragma: no branch``: coverage cannot see that a match over
    every member of an enum is exhaustive, and a granularity added without a
    case is a mypy error, not a silent fall-through.
    """

    granularity: Granularity
    anchor: date
    year_start: tuple[int, int] = (1, 1)
    first_weekday: int = 0

    # --- construction -----------------------------------------------------

    @classmethod
    def containing(
        cls,
        moment: date,
        granularity: Granularity = Granularity.WEEK,
        *,
        year_start: tuple[int, int] = (1, 1),
        first_weekday: int = 0,
    ) -> Period:
        """The period of the given granularity that contains ``moment``."""
        return cls(granularity, moment, year_start, first_weekday)

    # --- span -------------------------------------------------------------

    @property
    def start(self) -> date:
        """The first date in the span."""
        match self.granularity:
            case Granularity.DAY:
                return self.anchor
            case Granularity.WEEK:
                return week_start(self.anchor, first_weekday=self.first_weekday)
            case Granularity.MONTH:
                return self.anchor.replace(day=1)
            case Granularity.YEAR:  # pragma: no branch
                return self._year_start()

    @property
    def end(self) -> date:
        """The last date in the span, inclusive."""
        match self.granularity:
            case Granularity.DAY:
                return self.anchor
            case Granularity.WEEK:
                return self.start + timedelta(days=6)
            case Granularity.MONTH:
                last = calendar.monthrange(self.anchor.year, self.anchor.month)[1]
                return self.anchor.replace(day=last)
            case Granularity.YEAR:  # pragma: no branch
                # Asked of `leaveyear`, not recomputed: deriving the next start
                # from this one clamps twice, so a leave year beginning on 29
                # February would end a day early in a common year.
                return leaveyear.bounds(self.anchor, *self.year_start)[1]

    def _year_start(self) -> date:
        month, day = self.year_start
        return leaveyear.start_of(self.anchor, month, day)

    def days(self) -> list[date]:
        """Every date in the span, in order."""
        return days_between(self.start, self.end)

    def __len__(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, moment: date) -> bool:
        """True when ``moment`` falls inside the span."""
        return self.start <= moment <= self.end

    # --- movement ---------------------------------------------------------

    def shift(self, count: int) -> Period:
        """The period ``count`` spans forward, or backward when negative.

        The anchor keeps its place in the span where it can: the same weekday in
        a week, the same day number in a month, clamped to the last day of a
        shorter one, so stepping forward from 31 January lands on 28 February.

        Paging stops at the ends of the supported window, leaving the period
        where it is. That window is a year short of ``date``'s own at each end,
        because leave-year arithmetic reads a year either side of the date it is
        given: ``y`` then ``]`` from a date in 9998 would raise in
        :func:`flexi.domain.leaveyear.step`.
        """
        try:
            moved = self._stepped(count)
        except (OverflowError, ValueError):
            # Past what `date` itself holds. `leaveyear` and `add_months` both
            # meet that edge before the window below can be asked about it.
            return self
        if not SUPPORTED_FIRST <= moved <= SUPPORTED_LAST:
            return self
        return replace(self, anchor=moved)

    def _stepped(self, count: int) -> date:
        """Where the anchor lands ``count`` spans away."""
        match self.granularity:
            case Granularity.DAY:
                return self.anchor + timedelta(days=count)
            case Granularity.WEEK:
                return self.anchor + timedelta(weeks=count)
            case Granularity.MONTH:
                return add_months(self.anchor, count)
            case Granularity.YEAR:  # pragma: no branch
                # Asked of `leaveyear`, for the reason `end` is: twelve months
                # from a clamped 29 February lands inside the year it started
                # in, which would leave this key doing nothing.
                return leaveyear.step(self.anchor, *self.year_start, count)

    def zoom(self, granularity: Granularity) -> Period:
        """The same anchor, seen at a different width."""
        return replace(self, granularity=granularity)

    def go_to(self, moment: date) -> Period:
        """The same width, anchored on a different date."""
        return replace(self, anchor=moment)

    def with_year_start(self, year_start: tuple[int, int]) -> Period:
        """Use a new leave-year boundary without moving the period's anchor."""
        return replace(self, year_start=year_start)

    # --- presentation -----------------------------------------------------

    @property
    def label(self) -> str:
        """How the period names itself in a border title."""
        match self.granularity:
            case Granularity.DAY:
                return long_date(self.anchor)
            case Granularity.WEEK:
                return f"Week of {day_month(self.start)}"
            case Granularity.MONTH:
                return month_title(self.anchor.year, self.anchor.month)
            case Granularity.YEAR:  # pragma: no branch
                start = self._year_start()
                if self.year_start == (1, 1):
                    return str(start.year)
                return f"{start.year}/{str(start.year + 1)[-2:]}"
