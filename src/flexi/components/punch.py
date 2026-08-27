"""The punch strip, drawn. The bucketing is pure and lives in the domain.

:func:`render_strip` takes a style lookup rather than a widget, because the
records table paints strips into ``DataTable`` cells: mounting a widget per row
would cost a layout pass on the one thing that redraws on a timer.

An absence cell takes the colour of the type booked over that half of the day,
so a sick morning and an annual afternoon draw as two colours in one row.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any, ClassVar, Final

from rich.style import Style
from rich.text import Text
from textual.app import RenderResult
from textual.widget import Widget

from flexi.domain.ledger import DayLedger
from flexi.domain.punch import Cell, Window, covering_slices, edges, strip
from flexi.theme import CELL_GLYPHS

BASE_STYLES: Final[Mapping[Cell, str]] = MappingProxyType(
    {
        Cell.OFF: "punch--off",
        Cell.BREAK: "punch--break",
        Cell.TARGET: "punch--target",
        Cell.ABSENCE: "punch--annual",
        Cell.HOLIDAY: "punch--holiday",
        Cell.ON: "punch--on",
        Cell.LIVE: "punch--live",
    }
)

PUNCH_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "punch--off",
        "punch--on",
        "punch--live",
        "punch--break",
        "punch--target",
        "punch--holiday",
        "punch--annual",
        "punch--sick",
        "punch--toil",
        "punch--unpaid",
        "punch--other",
    }
)

StyleLookup = Callable[[str], Style]


def absence_tokens(ledger: DayLedger, count: int, window: Window) -> list[str | None]:
    """The colour token, if any, each cell should wear.

    The rule about which booking covers which cell belongs to the domain and is
    asked of it. This had its own copy, which also recomputed the cell
    boundaries `strip` had just worked out.
    """
    if not ledger.absences:
        return [None] * count
    bounds = edges(ledger.date, count, window)
    return [
        None if slice_ is None else slice_.type.token
        for slice_ in covering_slices(ledger, bounds)
    ]


def render_strip(
    ledger: DayLedger,
    width: int,
    window: Window,
    style_of: StyleLookup,
    *,
    now: datetime,
) -> Text:
    """One day as styled text, ready to be put in a cell or rendered by a widget."""
    cells = strip(ledger, width, window, now=now)
    tokens = absence_tokens(ledger, len(cells), window)
    text = Text(no_wrap=True, end="")
    for index, cell in enumerate(cells):
        style_name = BASE_STYLES[cell]
        token = tokens[index]
        if cell is Cell.ABSENCE and token is not None:
            style_name = f"punch--{token}"
        text.append(CELL_GLYPHS[cell], style_of(style_name))
    return text


class PunchStrip(Widget):
    """One working day as a row of cells, owning its own rectangle.

    Used where the strip is the point — the clock module, and the expanded row of
    the records table. The table's collapsed rows call :func:`render_strip`
    directly instead.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = set(PUNCH_CLASSES)

    def __init__(
        self,
        ledger: DayLedger | None = None,
        *,
        window: Window | None = None,
        now: datetime,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.ledger = ledger
        self.window = window or Window()
        self.now = now

    def set_ledger(
        self,
        ledger: DayLedger | None,
        *,
        window: Window | None = None,
        now: datetime,
    ) -> None:
        """Draw a different day."""
        self.ledger = ledger
        if window is not None:
            self.window = window
        self.now = now
        self.refresh()

    def render(self) -> RenderResult:
        if self.ledger is None:
            return Text("")
        return render_strip(
            self.ledger,
            self.content_size.width,
            self.window,
            self.get_component_rich_style,
            now=self.now,
        )
