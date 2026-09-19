"""The punch strip: a working day drawn as a row of cells.

    ─────────────████████████·············█████████────

A cell lights if any part of a session falls inside it, so the strip shows
presence, not proportion. Only the window it is handed is drawn: an evening
session under the default 07:00 to 19:00 draws an empty rail beside a row
reporting the hours.

Every result is a function of ``(ledger, width, window, now)`` and of the zone
`flexi.wallclock` is pinned to, which is the only outward import in
`flexi.domain`. A noun-phrase name returns a value, ``paint_*`` writes into the
cells it is handed, and a bare verb asks a question.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from itertools import pairwise

from flexi import wallclock
from flexi.domain.ledger import AbsenceSlice, DayLedger

__all__ = (
    "BUCKET_SIZES",
    "COARSE_CELLS",
    "MIN_CELLS",
    "Cell",
    "Window",
    "bucket_minutes",
    "cell_count",
    "cell_holding",
    "covering_slices",
    "edges",
    "overlaps",
    "paint_absences",
    "paint_breaks",
    "paint_live",
    "paint_sessions",
    "paint_target",
    "strip",
)

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
    """Between two sessions: away, but having arrived and not yet left."""

    TARGET = "target"
    """Where contracted hours will have been met, given today's breaks."""

    ABSENCE = "absence"
    """Covered by a booked absence."""

    HOLIDAY = "holiday"
    """A bank holiday. Covers the whole strip."""

    AMENDED = "amended"
    """On the clock, by a correction and not a punch.

    Ranks above an absence and below a live session.
    """

    ON = "on"
    """On the clock."""

    LIVE = "live"
    """On the clock right now: the leading edge of an open session."""


@dataclass(frozen=True, slots=True)
class Window:
    """The span of the day the strip draws, edge to edge.

    Work outside it is not drawn, so the defaults below are the hours an
    ordinary working day happens in, not a limit on what can be worked.
    """

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
    colours which cell.
    """
    span = window.minutes / count
    midnight = datetime.combine(day, time.min)
    first = wallclock.local(window.moment(midnight, 0.0))
    last = wallclock.local(window.moment(midnight, count * span))
    if first.utcoffset() == last.utcoffset():
        # No transition inside the window: every bound carries the offset the
        # two ends share, so fixed-offset arithmetic gives the same wall grid.
        return [first + timedelta(minutes=index * span) for index in range(count + 1)]
    # A transition inside the window: each bound is localised on its own, so
    # 02:00 on the October Sunday stays an hour further from midnight than 01:00.
    return [
        wallclock.local(window.moment(midnight, index * span))
        for index in range(count + 1)
    ]


def strip(
    ledger: DayLedger,
    width: int,
    window: Window | None = None,
    *,
    now: datetime,
) -> tuple[Cell, ...]:
    """Draw one day as a row of cells, never wider than ``width``.

    ``now`` is required. An open session ends at ``now``, so the caller has to
    say when the drawing is happening; `DayLedger.last_out` needs the same
    value for the same reason.
    """
    window = window or Window()
    count = cell_count(window, max(1, width))
    moment = now

    if ledger.is_holiday:
        return (Cell.HOLIDAY,) * count

    cells = [Cell.OFF] * count
    bounds = edges(ledger.date, count, window)

    # Layers in precedence order: each may overwrite the one before, which puts
    # a session on top of an absence and the live cell on top of both.
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
    """The index of the cell a moment falls in, or ``None`` if it is outside.

    ``bounds`` is sorted by construction, so this bisects.
    """
    index = bisect_right(bounds, moment) - 1
    return index if 0 <= index < len(bounds) - 1 else None


def covering_slices(
    ledger: DayLedger, bounds: list[datetime]
) -> list[AbsenceSlice | None]:
    """Which booking, if any, covers each cell, by the cell's midpoint.

    The midpoints depend only on the grid, so they are worked out once for the
    row and reused for every booking.
    """
    found: list[AbsenceSlice | None] = [None] * (len(bounds) - 1)
    if not ledger.absences:
        return found
    middles = [start + (end - start) / 2 for start, end in pairwise(bounds)]
    for slice_ in ledger.absences:
        for index, middle in enumerate(middles):
            if slice_.covers(middle):
                found[index] = slice_
    return found


def paint_absences(
    cells: list[Cell], ledger: DayLedger, bounds: list[datetime]
) -> None:
    """A cell is absent when a booking covers it."""
    for index, slice_ in enumerate(covering_slices(ledger, bounds)):
        if slice_ is not None:
            cells[index] = Cell.ABSENCE


def paint_sessions(
    cells: list[Cell], ledger: DayLedger, bounds: list[datetime], moment: datetime
) -> None:
    """A cell is on the clock when a session touches it.

    A corrected stretch takes the same colour as a punched one and a different
    fill.
    """
    for segment in ledger.segments:
        finish = segment.finish(moment)
        worked = Cell.AMENDED if segment.amended else Cell.ON
        for index in range(len(cells)):
            if overlaps(segment.start, finish, bounds, index):
                cells[index] = worked


def paint_breaks(cells: list[Cell], ledger: DayLedger, bounds: list[datetime]) -> None:
    """A break is only a break *between* two sessions.

    Time before the first clock-in and after the last clock-out stays ``OFF``.
    """
    for start, end in ledger.breaks:
        for index, cell in enumerate(cells):
            if cell is Cell.OFF and overlaps(start, end, bounds, index):
                cells[index] = Cell.BREAK


def paint_target(cells: list[Cell], ledger: DayLedger, bounds: list[datetime]) -> None:
    """Put the go-home tick on the first cell that is free to carry it."""
    leave_at = ledger.leave_at
    if leave_at is None or ledger.expected <= timedelta():
        return
    index = cell_holding(leave_at, bounds)
    if index is not None and cells[index] in {Cell.OFF, Cell.BREAK}:
        cells[index] = Cell.TARGET


def paint_live(
    cells: list[Cell], ledger: DayLedger, bounds: list[datetime], moment: datetime
) -> None:
    """Highlight the cell an open session is currently in."""
    if not ledger.is_open:
        return
    index = cell_holding(moment, bounds)
    if index is not None and cells[index] is Cell.ON:
        cells[index] = Cell.LIVE
