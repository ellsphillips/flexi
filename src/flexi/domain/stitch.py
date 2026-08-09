"""Laying a span of months out as one continuous grid.

A wall calendar pages month by month because paper has edges. A terminal does
not, so a leave year is drawn as one column that scrolls: months flow into each
other, and a fortnight spanning the end of July is a fortnight rather than two
halves the reader has to hold in their head.

The stitching is the whole trick, and it is arithmetic rather than drawing —
which is why it lives here, is pure, and is pinned by tests that count rows.

Every month starts on its own row so the weekday columns line up down the whole
year. That costs a partial row at each seam, which is the price of a grid you can
read a column of Mondays off.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from flexi.domain.format import long_date, short_date, stamp

DAYS_IN_WEEK = 7
MONTHS_IN_YEAR = 12
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
        return f"{MONTH_NAMES[self.month - 1]} {self.year}"

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

    @property
    def height(self) -> int:
        """Rows the block occupies, including its title."""
        return len(self.rows) + 1


def month_block(year: int, month: int, *, first_weekday: int = 0) -> MonthBlock:
    """One month laid out as whole weeks.

    Leading and trailing cells are blank rather than borrowed from the
    neighbouring month. A grid that showed the 30th of June twice — once in
    June's block and once in July's — would make a cursor ambiguous and a
    selection uncountable.
    """
    first = date(year, month, 1)
    length = calendar.monthrange(year, month)[1]
    lead = (first.weekday() - first_weekday) % DAYS_IN_WEEK

    cells: list[Cell] = [Cell(None)] * lead
    cells += [Cell(date(year, month, day)) for day in range(1, length + 1)]
    while len(cells) % DAYS_IN_WEEK:
        cells.append(Cell(None))

    rows = tuple(
        tuple(cells[index : index + DAYS_IN_WEEK])
        for index in range(0, len(cells), DAYS_IN_WEEK)
    )
    return MonthBlock(year, month, rows)


def stitch(start: date, end: date, *, first_weekday: int = 0) -> list[MonthBlock]:
    """Every month touched by the span, in order.

    Whole months, even when the span starts mid-month: a leave year beginning on
    the 20th of October still wants October drawn, or the days before it would
    have nowhere to be and the seam would land in the middle of a week.
    """
    blocks: list[MonthBlock] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        blocks.append(month_block(year, month, first_weekday=first_weekday))
        year, month = (year + 1, 1) if month == MONTHS_IN_YEAR else (year, month + 1)
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

    def days(self) -> Iterator[date]:
        current = self.start
        while current <= self.end:
            yield current
            current += timedelta(days=1)

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
