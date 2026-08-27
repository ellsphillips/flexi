"""Plotting a series onto a character grid, without a character in sight.

A terminal cell is not a pixel, and a chart drawn one glyph per sample is a
histogram whatever it is called. Braille is the way out: every cell carries a
two-by-four grid of dots, so a strip forty cells wide is eighty positions across
and thirty-two down, which is enough for a line to look like a line.

The whole module is arithmetic over integers and floats. It knows about dots,
columns and bounds; it does not know about Rich, Textual, colour or styling. A
:class:`Glyph` names the tone it wants and the widget decides what that means,
which is what keeps every rule here testable without mounting anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import accumulate, pairwise
from typing import Final

__all__ = (
    "BRAILLE_BASE",
    "DOT_BITS",
    "DOT_COLUMNS",
    "DOT_ROWS",
    "Bounds",
    "Canvas",
    "Glyph",
    "Mark",
    "Series",
    "bar_glyphs",
    "braille",
    "line_dots",
    "plot",
    "stack",
)

BRAILLE_BASE: Final = 0x2800
"""U+2800, the empty braille cell. Every pattern is this plus its dot bits."""

DOT_COLUMNS: Final = 2
DOT_ROWS: Final = 4
"""A braille cell is two dots across and four down."""

DOT_BITS: Final[tuple[tuple[int, ...], ...]] = (
    (0x01, 0x02, 0x04, 0x40),
    (0x08, 0x10, 0x20, 0x80),
)
"""Which bit lights the dot at ``[column][row]``, top row first.

Braille numbers its dots 1-2-3-7 down the left and 4-5-6-8 down the right, which
is not the order a raster wants. The table is the translation, written once.
"""

BAR_LEVELS: Final[str] = " ▁▂▃▄▅▆▇█"
"""A column of block glyphs, empty through full, one eighth at a time."""

EIGHTHS: Final = len(BAR_LEVELS) - 1


class Mark(StrEnum):
    """How a series is drawn."""

    LINE = "line"
    """Joined, at braille resolution. For a quantity that moves continuously."""

    BAR = "bar"
    """A column per sample. For a quantity that is counted rather than traced."""


@dataclass(frozen=True, slots=True)
class Series:
    """One run of values, and how it wants to be drawn.

    ``tone`` is a name, not a colour. The domain must not know what teal is, and
    the widget that does can look it up.
    """

    name: str
    values: tuple[float, ...]
    mark: Mark = Mark.LINE
    tone: str = "series"

    def __post_init__(self) -> None:
        if not self.name:
            msg = "A series needs a name; the legend has nothing else to show"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Glyph:
    """One character of the finished plot, and the tone it asks for."""

    char: str
    tone: str


@dataclass(frozen=True, slots=True)
class Bounds:
    """The value range a plot is drawn against.

    Examples:
        >>> Bounds.around([Series("a", (0.0, 5.0, 10.0))], stacked=False)
        Bounds(low=0.0, high=10.0)
    """

    low: float
    high: float

    def __post_init__(self) -> None:
        if self.high < self.low:
            msg = f"Bounds run backwards: {self.high} is below {self.low}"
            raise ValueError(msg)

    @classmethod
    def around(cls, series: Sequence[Series], *, stacked: bool) -> Bounds:
        """The range that fits every series, with zero always included.

        Zero is kept in view because these are hours against a contract: a
        chart of 7:20 to 7:30 that fills its height is a dramatic picture of
        nothing happening. Stacked bars are measured by their totals, which is
        what the reader is looking at.
        """
        readings = list(_readings(series, stacked=stacked))
        return cls(min(0.0, *readings), max(0.0, *readings)) if readings else cls(0, 0)

    @property
    def span(self) -> float:
        """How far the range runs. Never zero, so a division is always safe."""
        return self.high - self.low or 1.0

    def position(self, value: float, height: int) -> float:
        """Where a value sits, in rows from the bottom of a plot ``height`` tall.

        Examples:
            >>> Bounds(0.0, 10.0).position(5.0, 10)
            5.0
            >>> Bounds(0.0, 10.0).position(10.0, 10)
            10.0
        """
        return (value - self.low) / self.span * height


def _readings(series: Iterable[Series], *, stacked: bool) -> Iterable[float]:
    """Every value a plot has to fit, stacked series counted as their totals."""
    bars = [one for one in series if one.mark is Mark.BAR]
    others = [one for one in series if one.mark is not Mark.BAR]
    for one in others:
        yield from one.values
    if not bars:
        return
    if not stacked:
        for one in bars:
            yield from one.values
        return
    for column in zip(*(one.values for one in bars), strict=False):
        yield from accumulate(column)


def braille(bits: int) -> str:
    """The braille cell lighting exactly ``bits``.

    Examples:
        >>> braille(0)
        '⠀'
        >>> braille(DOT_BITS[0][0])
        '⠁'
    """
    return chr(BRAILLE_BASE + bits)


@dataclass(slots=True)
class Canvas:
    """A braille bitmap: ``width`` by ``height`` cells, eight dots in each.

    Mutable on purpose. Rasterising a line is a loop that lights one dot at a
    time, and rebuilding a frozen grid per dot would be a copy per pixel.

    Examples:
        >>> canvas = Canvas(2, 1)
        >>> canvas.light(0, 0)
        >>> canvas.rows()
        ['⡀⠀']
    """

    width: int
    height: int
    cells: list[list[int]] = field(init=False)

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            msg = f"A canvas needs positive extents, not {self.width}x{self.height}"
            raise ValueError(msg)
        self.cells = [[0] * self.width for _ in range(self.height)]

    @property
    def dot_width(self) -> int:
        return self.width * DOT_COLUMNS

    @property
    def dot_height(self) -> int:
        return self.height * DOT_ROWS

    def light(self, x: int, y: int) -> None:
        """Light the dot at ``(x, y)``, measured from the bottom left.

        Out-of-range dots are dropped rather than raising: a series is clipped
        by the box it is drawn in, and a line that leaves the top is a normal
        thing for a chart to be asked to draw.
        """
        if not (0 <= x < self.dot_width and 0 <= y < self.dot_height):
            return
        row = self.height - 1 - y // DOT_ROWS
        self.cells[row][x // DOT_COLUMNS] |= DOT_BITS[x % DOT_COLUMNS][
            DOT_ROWS - 1 - y % DOT_ROWS
        ]

    def rows(self) -> list[str]:
        """The canvas as lines of braille, top row first."""
        return ["".join(braille(cell) for cell in row) for row in self.cells]


def line_dots(values: Sequence[float], bounds: Bounds, canvas: Canvas) -> None:
    """Draw ``values`` as a joined line onto ``canvas``.

    The samples are spread across the full width and the gaps between them are
    filled in, so the result reads as one stroke rather than a row of dots. One
    value is a point; none is nothing.
    """
    if not values:
        return
    span = canvas.dot_width - 1
    step = span / (len(values) - 1) if len(values) > 1 else 0.0
    plotted = [
        (round(index * step), round(bounds.position(value, canvas.dot_height - 1)))
        for index, value in enumerate(values)
    ]
    for (x0, y0), (x1, y1) in pairwise(plotted) or ():
        _stroke(canvas, x0, y0, x1, y1)
    if len(plotted) == 1:
        canvas.light(*plotted[0])


def _stroke(canvas: Canvas, x0: int, y0: int, x1: int, y1: int) -> None:
    """Bresenham between two dots, inclusive of both."""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    step_x, step_y = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    error = dx + dy
    while True:
        canvas.light(x0, y0)
        if x0 == x1 and y0 == y1:
            return
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += step_x
        if doubled <= dx:
            error += dx
            y0 += step_y


def stack(series: Sequence[Series]) -> list[tuple[float, ...]]:
    """Running totals down each column, so segment *n* sits on the ones below.

    Examples:
        >>> stack([Series("a", (1.0, 2.0)), Series("b", (3.0, 4.0))])
        [(1.0, 4.0), (2.0, 6.0)]
    """
    columns = zip(*(one.values for one in series), strict=False)
    return [tuple(accumulate(column)) for column in columns]


def bar_glyphs(
    series: Sequence[Series], bounds: Bounds, width: int, height: int, *, stacked: bool
) -> list[list[Glyph | None]]:
    """Bars as a grid of glyphs, top row first, ``None`` where nothing is drawn.

    Each sample owns a slice of the width, so a fortnight in forty columns is
    drawn two cells wide rather than as a hairline with a gap beside it.
    """
    grid: list[list[Glyph | None]] = [[None] * width for _ in range(height)]
    if not series or width <= 0 or height <= 0:
        return grid
    samples = min(len(one.values) for one in series)
    if not samples:
        return grid

    tops = stack(series) if stacked else None
    for index in range(samples):
        for column in _slice_of(index, samples, width):
            floor = 0.0
            for depth, one in enumerate(series):
                top = tops[index][depth] if tops else one.values[index]
                _fill(grid, column, bounds, floor, top, one.tone, height)
                if stacked:
                    floor = top
    return grid


def _slice_of(index: int, samples: int, width: int) -> range:
    """The columns belonging to one sample, sharing the width out evenly."""
    start = index * width // samples
    return range(start, max(start + 1, (index + 1) * width // samples))


def _fill(
    grid: list[list[Glyph | None]],
    column: int,
    bounds: Bounds,
    floor: float,
    top: float,
    tone: str,
    height: int,
) -> None:
    """Paint one segment of one column, in eighths of a cell."""
    base = bounds.position(floor, height)
    crown = bounds.position(top, height)
    if crown <= base:
        return
    for row in range(height):
        covered = min(crown, row + 1) - max(base, row)
        if covered <= 0:
            continue
        level = max(1, round(covered * EIGHTHS))
        grid[height - 1 - row][column] = Glyph(BAR_LEVELS[level], tone)


def plot(
    series: Sequence[Series], width: int, height: int, *, stacked: bool = False
) -> list[list[Glyph | None]]:
    """Every series, drawn onto one grid of glyphs, top row first.

    Bars go down first and lines over them, because a line is the reading and
    bars are the context it is read against. Two lines share a grid by taking
    the cells the other left empty, which is what braille is for: a cell is
    eight dots and two strokes rarely want the same one.
    """
    bounds = Bounds.around(series, stacked=stacked)
    bars = [one for one in series if one.mark is Mark.BAR]
    grid = bar_glyphs(bars, bounds, width, height, stacked=stacked)

    for one in (item for item in series if item.mark is Mark.LINE):
        canvas = Canvas(width, height)
        line_dots(one.values, bounds, canvas)
        for row, drawn in enumerate(canvas.rows()):
            for column, char in enumerate(drawn):
                if char != braille(0):
                    grid[row][column] = Glyph(char, one.tone)
    return grid
