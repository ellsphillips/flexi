"""Laying a span of months out as one continuous grid.

A terminal has no page edges, so a leave year is one scrolling column: months
flow into each other and a fortnight spanning the end of July stays a fortnight.

Every month starts on its own row so the weekday columns line up down the whole
year. That costs a partial row at each seam, which is the price of a grid you
can read a column of Mondays off.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from flexi.domain.dates import add_months, days_between
from flexi.domain.format import long_date, month_title, short_date, stamp


@dataclass(frozen=True, slots=True)
class Cell:
    """One position in the grid.

    ``date`` is ``None`` where a month has not started yet or has already
    ended — the blanks at a seam.
    """

    date: date | None

    @property
    def filled(self) -> bool:
        return self.date is not None


@dataclass(frozen=True, slots=True)
class MonthBlock:
    """One month, as whole weeks of seven cells."""

    year: int
    month: int
    rows: tuple[tuple[Cell, ...], ...]

    @property
    def title(self) -> str:
        return month_title(self.year, self.month)

    @property
    def first(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last(self) -> date:
        return date(
            self.year, self.month, calendar.monthrange(self.year, self.month)[1]
        )

    def contains(self, when: date) -> bool:
        return (when.year, when.month) == (self.year, self.month)


def month_block(year: int, month: int, *, first_weekday: int = 0) -> MonthBlock:
    """One month laid out as whole weeks.

    Leading and trailing cells are blank rather than borrowed from the
    neighbouring month. A grid that showed the 30th of June twice — once in
    June's block and once in July's — would make a cursor ambiguous and a
    selection uncountable.
    """
    # `monthdayscalendar` already pads both ends with 0 and hands back whole
    # weeks rotated to `first_weekday`, which is the lead, the tail and the
    # chunking this used to compute for itself in nine lines.
    weeks = calendar.Calendar(first_weekday).monthdayscalendar(year, month)
    rows = tuple(
        tuple(Cell(date(year, month, day) if day else None) for day in week)
        for week in weeks
    )
    return MonthBlock(year, month, rows)


def stitch(start: date, end: date, *, first_weekday: int = 0) -> list[MonthBlock]:
    """Every month touched by the span, in order.

    Whole months, even when the span starts mid-month: a leave year beginning on
    the 20th of October still wants October drawn, or the days before it would
    have nowhere to be and the seam would land in the middle of a week.
    """
    blocks: list[MonthBlock] = []
    first = start.replace(day=1)
    while first <= end.replace(day=1):
        blocks.append(month_block(first.year, first.month, first_weekday=first_weekday))
        first = add_months(first, 1)
    return blocks


def weekday_initials(first_weekday: int = 0) -> tuple[str, ...]:
    """The column headings, rotated to the configured first day."""
    initials = ("M", "T", "W", "T", "F", "S", "S")
    return initials[first_weekday:] + initials[:first_weekday]


@dataclass(frozen=True, slots=True)
class Selection:
    """The cursor, and how far it has been extended.

    Held as an anchor and a head rather than a start and an end, because a
    selection extended backwards and then forwards has to return to one day
    rather than inverting.
    """

    anchor: date
    head: date

    @classmethod
    def at(cls, when: date) -> Selection:
        return cls(when, when)

    @property
    def start(self) -> date:
        return min(self.anchor, self.head)

    @property
    def end(self) -> date:
        return max(self.anchor, self.head)

    @property
    def single(self) -> bool:
        return self.anchor == self.head

    def __len__(self) -> int:
        return (self.end - self.start).days + 1

    def __contains__(self, when: object) -> bool:
        return isinstance(when, date) and self.start <= when <= self.end

    def days(self) -> list[date]:
        """Every date the selection covers, in order."""
        return days_between(self.start, self.end)

    def move(self, days: int) -> Selection:
        """Move the whole thing, collapsing it back to one day."""
        return Selection.at(self.head + timedelta(days=days))

    def extend(self, days: int) -> Selection:
        """Move the head, keeping the anchor where it was."""
        return Selection(self.anchor, self.head + timedelta(days=days))

    def collapse(self) -> Selection:
        """Back to one day, at the head."""
        return Selection.at(self.head)

    def go_to(self, when: date) -> Selection:
        return Selection.at(when)

    def label(self) -> str:
        """How the selection names itself."""
        if self.single:
            return long_date(self.anchor)
        if self.start.month == self.end.month:
            return f"{stamp(self.start, '%a %-d')} – {long_date(self.end)}"
        return f"{short_date(self.start)} – {long_date(self.end)}"
