"""The temporal view: which span of dates the dashboard is showing.

the reference application models a period as *an offset from today*, which cannot express next
month — it rings the terminal bell at you instead. Flexi books leave in the
future, so a period is an **anchor** plus a granularity, and every operation is
defined in terms of moving or reinterpreting that anchor.

Two behaviours follow from the anchor, and they are what make the control feel
right under the hand:

**Zooming keeps the anchor.** Standing on Thursday of week 24 and pressing `m`
gives you June; pressing `w` again gives you week 24 back, not the week
containing the first of the month. The anchor never moved, so the user's place
never moved.

**Going to today resets the anchor, not the granularity.** Someone who has
chosen a month view and pressed `t` wants *this month*.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


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


def _clamp_day(year: int, month: int, day: int) -> date:
    """The given day of the given month, or its last day if it is shorter."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _add_months(anchor: date, months: int) -> date:
    total = (anchor.year * 12 + anchor.month - 1) + months
    return _clamp_day(total // 12, total % 12 + 1, anchor.day)


@dataclass(frozen=True, slots=True)
class Period:
    """A span of dates, identified by any date inside it.

    Args:
        granularity: How wide the span is.
        anchor: A date inside the span. Operations move or reinterpret this,
            never a separate cursor, which is what keeps zooming lossless.
        year_start: ``(month, day)`` the leave year turns over on. Only affects
            :attr:`Granularity.YEAR`.
        first_weekday: ``0`` for Monday. Only affects :attr:`Granularity.WEEK`.
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
                back = (self.anchor.weekday() - self.first_weekday) % 7
                return self.anchor - timedelta(days=back)
            case Granularity.MONTH:
                return self.anchor.replace(day=1)
            case Granularity.YEAR:
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
            case Granularity.YEAR:
                start = self._year_start()
                return _clamp_day(start.year + 1, start.month, start.day) - timedelta(
                    days=1
                )

    def _year_start(self) -> date:
        month, day = self.year_start
        this_year = _clamp_day(self.anchor.year, month, day)
        if self.anchor >= this_year:
            return this_year
        return _clamp_day(self.anchor.year - 1, month, day)

    def days(self) -> Iterator[date]:
        """Every date in the span, in order."""
        current, last = self.start, self.end
        while current <= last:
            yield current
            current += timedelta(days=1)

    def __len__(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, moment: date) -> bool:
        """True when ``moment`` falls inside the span."""
        return self.start <= moment <= self.end

    def is_current(self, today: date) -> bool:
        """True when the span contains ``today``."""
        return self.contains(today)

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
                return replace(self, anchor=_add_months(self.anchor, count))
            case Granularity.YEAR:
                return replace(self, anchor=_add_months(self.anchor, count * 12))

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
                return self.anchor.strftime("%a %-d %b %Y")
            case Granularity.WEEK:
                return f"Week of {self.start.strftime('%-d %b')}"
            case Granularity.MONTH:
                return f"{MONTH_NAMES[self.anchor.month - 1]} {self.anchor.year}"
            case Granularity.YEAR:
                start = self._year_start()
                if self.year_start == (1, 1):
                    return str(start.year)
                return f"{start.year}/{str(start.year + 1)[-2:]}"

    @property
    def short_label(self) -> str:
        """A form that fits a narrow subtitle."""
        match self.granularity:
            case Granularity.DAY:
                return self.anchor.strftime("%-d %b")
            case Granularity.WEEK:
                return self.start.strftime("%-d %b")
            case Granularity.MONTH:
                return self.anchor.strftime("%b %Y")
            case Granularity.YEAR:
                return self.label
