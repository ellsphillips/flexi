"""The punch strip, drawn. The bucketing is pure and lives in the domain.

:func:`render_strip` takes a style lookup rather than a widget, because the
records table paints strips into ``DataTable`` cells: mounting a widget per row
would cost a layout pass on the one thing that redraws on a timer.

An absence cell takes the colour of the type booked over that half of the day,
so a sick morning and an annual afternoon draw as two colours in one row.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar, Final

from rich.style import Style
from rich.text import Text
from textual.app import RenderResult
from textual.widget import Widget

from flexi.domain.ledger import DayLedger
from flexi.domain.punch import Cell, Window, edges, strip

GLYPHS: Final[dict[Cell, str]] = {
    Cell.OFF: "─",
    Cell.BREAK: "·",
    Cell.TARGET: "┊",
    Cell.ABSENCE: "▓",
    Cell.HOLIDAY: "░",
    Cell.ON: "█",
    Cell.LIVE: "▌",
}

BASE_STYLES: Final[dict[Cell, str]] = {
    Cell.OFF: "punch--off",
    Cell.BREAK: "punch--break",
    Cell.TARGET: "punch--target",
    Cell.ABSENCE: "punch--annual",
    Cell.HOLIDAY: "punch--holiday",
    Cell.ON: "punch--on",
    Cell.LIVE: "punch--live",
}

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
    """Which absence type, if any, covers each cell.

    Fast path first: most days have no absence at all, and walking the cell
    boundaries for every row of a month view to discover that would be the
    table's slowest loop for no result.
    """
    if not ledger.absences:
        return [None] * count
    bounds = edges(ledger.date, count, window)
    tokens: list[str | None] = [None] * count
    for slice_ in ledger.absences:
        for index in range(count):
            middle = bounds[index] + (bounds[index + 1] - bounds[index]) / 2
            if slice_.covers(middle):
                tokens[index] = slice_.type.token
    return tokens


def render_strip(
    ledger: DayLedger,
    width: int,
    window: Window,
    style_of: StyleLookup,
    now: datetime | None = None,
) -> Text:
    """One day as styled text, ready to be put in a cell or rendered by a widget."""
    cells = strip(ledger, width, window, now)
    tokens = absence_tokens(ledger, len(cells), window)
    text = Text(no_wrap=True, end="")
    for index, cell in enumerate(cells):
        style_name = BASE_STYLES[cell]
        token = tokens[index]
        if cell is Cell.ABSENCE and token is not None:
            style_name = f"punch--{token}"
        text.append(GLYPHS[cell], style_of(style_name))
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
        now: datetime | None = None,
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
        now: datetime | None = None,
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
            self.now,
        )
