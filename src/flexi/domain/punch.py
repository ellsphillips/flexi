"""The punch strip: a working day drawn as a row of cells.

    ─────────────████████████·············█████████────

Two behaviours look like bugs and are not. The strip shows presence, not
proportion: a cell lights if any part of a session falls inside it, so a short
session is overstated rather than lost. And it coarsens rather than truncating,
falling back to three cells below :data:`MIN_CELLS` rather than claiming a
precision it cannot draw.

Everything is a pure function of ``(ledger, width, window, now)``, which is what
lets one implementation draw a table cell, an expanded row and a week ribbon.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from itertools import pairwise

from flexi import wallclock
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

    def moment(self, day: datetime, offset_minutes: float) -> datetime:
        """A datetime ``offset_minutes`` into the window on ``day``."""
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
    # Each bound localised on its own, so the grid stays a *wall* grid: on the
    # October Sunday 02:00 is an hour further from midnight than 01:00 was.
    return [
        wallclock.local(window.moment(midnight, index * span))
        for index in range(count + 1)
    ]


def strip(
    ledger: DayLedger,
    width: int,
    window: Window | None = None,
    now: datetime | None = None,
) -> tuple[Cell, ...]:
    """Draw one day as a row of cells, never wider than ``width``.

    ``now`` defaults to the ledger's last clock-out, so a historical day draws
    identically however often it is redrawn.
    """
    window = window or Window()
    count = cell_count(window, max(1, width))
    moment = now or wallclock.local(datetime.combine(ledger.date, time.min))

    if ledger.is_holiday:
        return (Cell.HOLIDAY,) * count

    cells = [Cell.OFF] * count
    bounds = edges(ledger.date, count, window)

    # Layers, painted in order. Each may overwrite the one before it, which is
    # what puts a session on top of an absence and the live cell on top of both.
    paint_absences(cells, ledger, bounds)
    paint_sessions(cells, ledger, bounds, moment)
    paint_breaks(cells, ledger, bounds)
    paint_target(cells, ledger, bounds)
    paint_live(cells, ledger, bounds, moment)
    return tuple(cells)


def overlaps(
    start: datetime, end: datetime, bounds: list[datetime], index: int
) -> bool:
    """Whether a span touches the cell at ``index``."""
    return start < bounds[index + 1] and end > bounds[index]


def cell_holding(moment: datetime, bounds: list[datetime]) -> int | None:
    """Which cell a moment falls in, or ``None`` if it falls outside the strip.

    Through `bisect`, because the two callers each walked every cell to find
    the one -- and the bounds are sorted, which is the whole precondition the
    module already relies on.
    """
    index = bisect.bisect_right(bounds, moment) - 1
    return index if 0 <= index < len(bounds) - 1 else None


def paint_absences(
    cells: list[Cell], ledger: DayLedger, bounds: list[datetime]
) -> None:
    """A cell is absent when its midpoint falls inside a booked portion."""
    for slice_ in ledger.absences:
        for index, (opens, closes) in enumerate(pairwise(bounds)):
            if slice_.covers(opens + (closes - opens) / 2):
                cells[index] = Cell.ABSENCE


def paint_sessions(
    cells: list[Cell], ledger: DayLedger, bounds: list[datetime], moment: datetime
) -> None:
    for segment in ledger.segments:
        finish = segment.finish(moment)
        for index in range(len(cells)):
            if overlaps(segment.start, finish, bounds, index):
                cells[index] = Cell.ON


def paint_breaks(cells: list[Cell], ledger: DayLedger, bounds: list[datetime]) -> None:
    """A break is only a break *between* two sessions.

    Time before arriving and after leaving is not being away, it is not being
    at work, so it stays off rather than reading as a three-hour lunch.
    """
    for start, end in ledger.breaks():
        for index in range(len(cells)):
            if cells[index] is Cell.OFF and overlaps(start, end, bounds, index):
                cells[index] = Cell.BREAK


def paint_target(cells: list[Cell], ledger: DayLedger, bounds: list[datetime]) -> None:
    """Put the go-home tick on the first cell that is free to carry it."""
    leave_at = ledger.leave_at()
    if leave_at is None or ledger.expected <= timedelta():
        return
    index = cell_holding(leave_at, bounds)
    if index is not None and cells[index] in {Cell.OFF, Cell.BREAK}:
        cells[index] = Cell.TARGET


def paint_live(
    cells: list[Cell],
    ledger: DayLedger,
    bounds: list[datetime],
    moment: datetime,
) -> None:
    """Highlight the cell an open session is currently in."""
    if not ledger.is_open:
        return
    index = cell_holding(moment, bounds)
    if index is not None and cells[index] is Cell.ON:
        cells[index] = Cell.LIVE
