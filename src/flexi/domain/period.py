"""Which span of dates is on screen.

A period is an anchor plus a granularity rather than an offset from today: an
offset cannot express next month, and Flexi books leave in the future.

Zooming keeps the anchor, so ``m`` then ``w`` returns to the week you were
standing on rather than the week containing the first of the month. Going to
today resets the anchor and not the granularity.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum

from flexi.domain import leaveyear
from flexi.domain.dates import add_months, days_between, week_start
from flexi.domain.format import day_month, long_date, month_title


class Granularity(StrEnum):
    """The span a period covers."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"

    @property
    def label(self) -> str:
        """The name shown to a reader."""
        return self.value.capitalize()

    def next(self) -> Granularity:
        """The next granularity in the cycle ``day → week → month → year → day``."""
        order: list[Granularity] = list(Granularity)
        return order[(order.index(self) + 1) % len(order)]

    def previous(self) -> Granularity:
        """The previous granularity in the cycle."""
        order: list[Granularity] = list(Granularity)
        return order[(order.index(self) - 1) % len(order)]


@dataclass(frozen=True, slots=True)
class Period:
    """A span of dates, identified by any date inside it.

    Operations move or reinterpret ``anchor`` rather than a separate cursor, which
    is what keeps zooming lossless. ``year_start`` affects only
    :attr:`Granularity.YEAR`, ``first_weekday`` only :attr:`Granularity.WEEK`.

    Every ``match self.granularity`` below ends on ``case Granularity.YEAR``
    carrying ``# pragma: no branch``. Coverage cannot see that a match over
    every member of an enum is exhaustive, so it reports the arm that never
    matches as a missed branch, and blames the last ``case`` for it. Adding a
    fifth granularity without a case would be a mypy error rather than a silent
    fall-through, which is what makes the pragma safe to write.
    """

    granularity: Granularity
    anchor: date
    year_start: tuple[int, int] = (1, 1)
    first_weekday: int = 0

    # -- construction ------------------------------------------------------

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

    # -- span --------------------------------------------------------------

    @property
    def start(self) -> date:
        """The first date in the span."""
        match self.granularity:
            case Granularity.DAY:
                return self.anchor
            case Granularity.WEEK:
                return week_start(self.anchor, self.first_weekday)
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
                # Asked of `leaveyear`, not recomputed. Deriving the next start
                # from *this* start clamps twice: a leave year beginning on 29
                # February starts on the 28th in a common year, and taking the
                # 28th forward gave 28 February rather than 29, so the year
                # ended a day early and the 28th belonged to neither year. On
                # screen it simply vanished, while every service — which does
                # ask `leaveyear` — still counted it.
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

    # -- movement ----------------------------------------------------------

    def shift(self, count: int) -> Period:
        """The period ``count`` spans forward, or backward when negative.

        The anchor keeps its position within the span where it can — the same
        weekday in a week, the same day number in a month, clamped to the last
        day of a shorter one, so stepping forward from 31 January lands on
        28 February rather than raising.
        """
        match self.granularity:
            case Granularity.DAY:
                return replace(self, anchor=self.anchor + timedelta(days=count))
            case Granularity.WEEK:
                return replace(self, anchor=self.anchor + timedelta(weeks=count))
            case Granularity.MONTH:
                return replace(self, anchor=add_months(self.anchor, count))
            case Granularity.YEAR:  # pragma: no branch
                # Asked of `leaveyear`, for the reason `end` is: twelve months
                # from a clamped 29 February is a date inside the year it came
                # from, so this key used to do nothing at all.
                anchor = leaveyear.step(self.anchor, *self.year_start, count)
                return replace(self, anchor=anchor)

    def zoom(self, granularity: Granularity) -> Period:
        """The same anchor, seen at a different width."""
        return replace(self, granularity=granularity)

    def go_to(self, moment: date) -> Period:
        """The same width, anchored on a different date."""
        return replace(self, anchor=moment)

    # -- presentation ------------------------------------------------------

    @property
    def label(self) -> str:
        """How the period names itself in a border title.

        A day inside the current year drops the year, because the year is
        already in the header and a title that repeats it is noise.
        """
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
