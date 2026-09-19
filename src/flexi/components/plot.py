"""The plot: lines, bars and stacked bars on one set of axes.

The geometry is in :mod:`flexi.domain.plot`. This module turns its grid of
:class:`~flexi.domain.plot.Glyph` into a styled ``Text``, hangs an axis down the
left and a legend underneath, and measures what room is left for the drawing.

A tone is a name until it reaches this file: `tone="annual"` gets whatever
`plot--annual` is defined as, so the palette stays in one place.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar, Final, Unpack

from rich.style import Style
from rich.text import Text
from textual.widget import Widget

from flexi.components.options import WidgetOptions
from flexi.domain.format import hm_hours
from flexi.domain.plot import Bounds, Glyph, Mark, Series, plot

__all__ = (
    "AXIS_WIDTH",
    "LEGEND_MARKS",
    "LEGEND_MIN_SERIES",
    "MIN_PLOT_HEIGHT",
    "PLOT_TONES",
    "Plot",
)

PLOT_TONES: Final[frozenset[str]] = frozenset(
    {"series", "compare", "target", "annual", "sick", "toil", "unpaid", "other"}
)
"""Every tone a series may ask for. A name the stylesheet does not define is a
black-on-black series, so the set is closed and checked."""

AXIS_WIDTH: Final = 6
"""Room for an axis label: five characters and the space after it."""

MIN_PLOT_HEIGHT: Final = 2
"""Below this, in either direction, there is no drawing left after the legend
and the axis, so the plot is not drawn."""

LEGEND_MIN_SERIES: Final = 2
"""One series needs no legend: the panel title names it."""

LEGEND_MARKS: Final[dict[Mark, str]] = {Mark.LINE: "──", Mark.BAR: "██"}
"""How a series is shown in the legend: as the thing it is drawn with."""


class Plot(Widget):
    """One set of axes carrying any number of series.

    Which mark a series is drawn with is the series' own business, so one set of
    axes can carry lines and bars together.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        *(f"plot--{tone}" for tone in PLOT_TONES),
        "plot--axis",
        "plot--legend",
        "plot--rule",
    }

    def __init__(self, **kwargs: Unpack[WidgetOptions]) -> None:
        super().__init__(**kwargs)
        self.series: tuple[Series, ...] = ()
        self.stacked = False
        self.rule: float | None = None
        self.empty_message = "Nothing to plot yet"

    def show(
        self,
        series: Sequence[Series],
        *,
        stacked: bool = False,
        rule: float | None = None,
        empty_message: str | None = None,
    ) -> None:
        """Draw these series, replacing whatever was there.

        An unknown tone is refused here, not rendered: it resolves to the
        widget's own style, which on this ground is a series drawn in the
        background colour.
        """
        unknown = {one.tone for one in series} - PLOT_TONES
        if unknown:
            msg = f"No plot tone named {', '.join(sorted(unknown))}"
            raise ValueError(msg)
        self.series = tuple(series)
        self.stacked = stacked
        self.rule = rule
        if empty_message is not None:
            self.empty_message = empty_message
        self.refresh()

    def render(self) -> Text:
        legend = self.legend()
        height = self.content_size.height - bool(legend.plain)
        width = self.content_size.width - AXIS_WIDTH
        if not self.series or height < MIN_PLOT_HEIGHT or width < MIN_PLOT_HEIGHT:
            return Text(self.empty_message, style=self._style("plot--legend"))

        grid = plot(self.series, width, height, stacked=self.stacked, rule=self.rule)
        drawn = Text()
        for row, cells in enumerate(grid):
            drawn.append_text(self._axis_label(row, height))
            drawn.append_text(_row(cells, self._style))
            drawn.append("\n")
        drawn.append_text(legend)
        return drawn

    # -- the furniture -----------------------------------------------------

    def bounds(self) -> tuple[float, float]:
        """The range the axis is labelled with, low first."""
        span = Bounds.around(self.series, stacked=self.stacked)
        if self.rule is None:
            return span.low, span.high
        return min(span.low, self.rule), max(span.high, self.rule)

    def _axis_label(self, row: int, height: int) -> Text:
        """A figure against the top and bottom rows, and nothing between them."""
        style = self._style("plot--axis")
        if row == 0:
            return Text(f"{hm_hours(self.bounds()[1]):>5} ", style=style)
        if row == height - 1:
            return Text(f"{hm_hours(self.bounds()[0]):>5} ", style=style)
        return Text(" " * AXIS_WIDTH, style=style)

    def legend(self) -> Text:
        """Every series, in the mark it is drawn with. Empty for a single one.

        Named while the names fit, marks alone when they do not: a legend wider
        than the panel is truncated by the terminal, which drops whichever
        series is listed last and says nothing about having done it.
        """
        if len(self.series) < LEGEND_MIN_SERIES:
            return Text()
        named = self.entries(named=True)
        room = self.content_size.width
        return named if len(named.plain) <= room else self.entries(named=False)

    def entries(self, *, named: bool) -> Text:
        """The legend row, with the series named or as marks alone."""
        legend = Text(" " * AXIS_WIDTH)
        for index, one in enumerate(self.series):
            if index:
                legend.append("  " if named else " ", style=self._style("plot--legend"))
            legend.append(
                LEGEND_MARKS[one.mark], style=self._style(f"plot--{one.tone}")
            )
            if named:
                legend.append(f" {one.name}", style=self._style("plot--legend"))
        return legend

    def _style(self, name: str) -> Style:
        return self.get_component_rich_style(name)


def _row(cells: Sequence[Glyph | None], style: Callable[[str], Style]) -> Text:
    """One line of the drawing, each run of glyphs in the tone it asked for."""
    line = Text()
    for cell in cells:
        if cell is None:
            line.append(" ")
        else:
            line.append(cell.char, style=style(f"plot--{cell.tone}"))
    return line
