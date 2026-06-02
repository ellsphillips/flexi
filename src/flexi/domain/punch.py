"""The punch strip: a working day drawn as a row of cells.

This is Flexi's signature element and the reason the interface reads as a time
card rather than a dashboard. A day becomes one line:

    ─────────────████████████·············█████████────

on the clock, a break, on the clock again, and the window either side of it.
Seven of them stacked on a shared time axis makes the shape of a week legible in
a way no column of totals is.

Two decisions are worth stating because they look like bugs otherwise.

**The strip shows presence, not proportion.** A cell lights if *any* part of a
session falls inside it, so a five-minute session at 30-minute resolution fills a
whole cell rather than disappearing. Overstating a short session is a smaller lie
than a strip that says you were never there.

**It coarsens, it never truncates.** Given less width, the strip picks a bigger
bucket so the window still spans the row. Below :data:`MIN_CELLS` columns it
falls back to three cells — morning, afternoon, evening — rather than claiming a
precision it cannot draw.

Everything here is a pure function of ``(ledger, width, window, now)``, which is
what lets the same code draw a full-width expanded row, a one-line table cell
and a week ribbon, and what lets the tests pin exact strings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from flexi.domain.ledger import DayLedger

BUCKET_SIZES: tuple[int, ...] = (5, 10, 15, 20, 30, 60)
"""Bucket widths in minutes, finest first. The strip takes the first that fits."""

MIN_CELLS = 12
"""Below this width the strip degrades to a three-cell summary."""

COARSE_CELLS = 3


class Cell(StrEnum):
    """What one cell of the strip is reporting.

    The order is the precedence order, lowest first: a later state overwrites an
    earlier one when both apply to the same cell.
    """

    OFF = "off"
    """Inside the window, and not at work."""

    BREAK = "break"
    """Between two sessions — away, but having arrived and not yet left."""

    TARGET = "target"
    """Where contracted hours will have been met, given today's breaks."""

    ABSENCE = "absence"
    """Covered by a booked absence."""

    HOLIDAY = "holiday"
    """A bank holiday. Covers the whole strip."""

    ON = "on"
    """On the clock."""

    LIVE = "live"
    """On the clock right now — the leading edge of an open session."""


@dataclass(frozen=True, slots=True)
class Window:
    """The span of the day the strip draws, edge to edge."""

    start: time = time(7, 0)
    end: time = time(19, 0)

    @classmethod
    def parse(cls, start: str, end: str) -> Window:
        """Build a window from two ``HH:MM`` strings."""
        return cls(time.fromisoformat(start), time.fromisoformat(end))

    @property
    def minutes(self) -> int:
        """How many minutes the window spans."""
        start = self.start.hour * 60 + self.start.minute
        end = self.end.hour * 60 + self.end.minute
        return max(1, end - start)

    def moment(self, day: datetime | None, offset_minutes: float) -> datetime:
        """A datetime ``offset_minutes`` into the window on ``day``."""
        assert day is not None
        base = day.replace(
            hour=self.start.hour, minute=self.start.minute, second=0, microsecond=0
        )
        return base + timedelta(minutes=offset_minutes)


def bucket_minutes(window: Window, width: int) -> int:
    """The finest bucket size whose cells fit in ``width`` columns."""
    for size in BUCKET_SIZES:
        if math.ceil(window.minutes / size) <= width:
            return size
    return BUCKET_SIZES[-1]


def cell_count(window: Window, width: int) -> int:
    """How many cells the strip will draw at this width."""
    if width < MIN_CELLS:
        return COARSE_CELLS
    return math.ceil(window.minutes / bucket_minutes(window, width))


def edges(day: date, count: int, window: Window) -> list[datetime]:
    """The ``count + 1`` moments that bound the strip's cells.

    Public because the widget needs the same boundaries to decide which absence
    colours which cell, and two implementations of the same bucketing would
    drift the first time either changed.
    """
    span = window.minutes / count
    midnight = datetime.combine(day, time.min)
    return [window.moment(midnight, index * span) for index in range(count + 1)]


def strip(
    ledger: DayLedger,
    width: int,
    window: Window | None = None,
    now: datetime | None = None,
) -> tuple[Cell, ...]:
    """Draw one day as a row of cells.

    Args:
        ledger: The day to draw.
        width: How many columns are available. The result is never wider.
        window: The span of the day to cover. Defaults to 07:00–19:00.
        now: The moment to treat as current, for the live edge and for any open
            session's length. Defaults to the ledger's last clock-out, so a
            historical day draws identically whenever it is redrawn.

    Returns:
        Between three and ``width`` cells, left to right.
    """
    window = window or Window()
    count = cell_count(window, max(1, width))
    moment = now or datetime.combine(ledger.date, time.min)

    cells = [Cell.OFF] * count

    if ledger.is_holiday:
        return tuple([Cell.HOLIDAY] * count)

    bounds = edges(ledger.date, count, window)

    for slice_ in ledger.absences:
        for index in range(count):
            middle = bounds[index] + (bounds[index + 1] - bounds[index]) / 2
            if slice_.covers(middle):
                cells[index] = Cell.ABSENCE

    for segment in ledger.segments:
        finish = segment.finish(moment)
        for index in range(count):
            if segment.start < bounds[index + 1] and finish > bounds[index]:
                cells[index] = Cell.ON

    # A break is only a break between two sessions; time before arriving and
    # after leaving is not being away, it is not being at work.
    for start, end in ledger.breaks():
        for index in range(count):
            if (
                cells[index] is Cell.OFF
                and start < bounds[index + 1]
                and end > bounds[index]
            ):
                cells[index] = Cell.BREAK

    _mark_target(cells, ledger, bounds)
    _mark_live(cells, ledger, bounds, moment)
    return tuple(cells)


def _mark_target(cells: list[Cell], ledger: DayLedger, edges: list[datetime]) -> None:
    """Put the go-home tick on the first cell that is free to carry it."""
    leave_at = ledger.leave_at()
    if leave_at is None or ledger.expected <= timedelta():
        return
    for index in range(len(cells)):
        if edges[index] <= leave_at < edges[index + 1]:
            if cells[index] in {Cell.OFF, Cell.BREAK}:
                cells[index] = Cell.TARGET
            return


def _mark_live(
    cells: list[Cell],
    ledger: DayLedger,
    edges: list[datetime],
    moment: datetime,
) -> None:
    """Highlight the cell an open session is currently in."""
    if not ledger.is_open:
        return
    for index in range(len(cells)):
        if edges[index] <= moment < edges[index + 1] and cells[index] is Cell.ON:
            cells[index] = Cell.LIVE
            return
