"""The calendar: where you are in time, and how to get somewhere else.

The hardest thing this widget does is say *where you are* without saying it four
times. There are three separate facts on screen — today, the selected day, and
the extent of the current period — and a naive design gives each its own colour
until the grid is a mess of highlights that nobody can read.

So each fact gets a different *device*:

* **the period** tints the ground of the rows or cells it covers,
* **the selection** reverses one cell,
* **today** is underlined,

which means all three can be true of one cell and it still reads. A day type
sits on top of that as the digit's colour, never as its background, so the
markers never fight the position.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal
from textual.widgets import Button, Label

from flexi import wallclock
from flexi.components.modules.base import Module
from flexi.config import CONFIG
from flexi.constants import DayKind
from flexi.domain.ledger import DayLedger
from flexi.domain.period import Granularity, Period
from flexi.messages import DateSelected, Scope

WEEKS = 6
DAYS = 7

KIND_CLASSES: dict[DayKind, str] = {
    DayKind.HOLIDAY: "day-holiday",
    DayKind.ABSENT: "day-absent",
    DayKind.PARTIAL: "day-partial",
    DayKind.WEEKEND: "day-weekend",
    DayKind.WORKING: "day-working",
}


class CalendarModule(Module):
    """A month grid that drives, and reflects, the dashboard's period."""

    WATCHES: ClassVar[Scope] = Scope.ALL

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left", "move(-1)", "Previous day", show=False),
        Binding("right", "move(1)", "Next day", show=False),
        Binding("up", "move(-7)", "Previous week", show=False),
        Binding("down", "move(7)", "Next week", show=False),
        Binding("comma", "month(-1)", "Previous month", show=False),
        Binding("full_stop", "month(1)", "Next month", show=False),
        Binding("enter", "select", "Go to day", show=False),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            id="calendar-module",
            title="Calendar",
            subtitle=f"← {CONFIG.hotkeys.period_cycle} →",
            **kwargs,
        )
        self._visible = wallclock.today().replace(day=1)
        self._last_anchor: date | None = None

    def compose(self) -> ComposeResult:
        with Container(classes="month-selector"):
            yield Button("‹", id="calendar-prev", classes="-quiet")
            yield Label("", classes="current-filter-label", id="calendar-label")
            yield Button("›", id="calendar-next", classes="-quiet")
        with Container(classes="calendar"):
            with Horizontal(classes="calendar-dotw-row"):
                for initial in ("M", "T", "W", "T", "F", "S", "S"):
                    yield Label(initial)
            for week in range(WEEKS):
                with Horizontal(classes="calendar-row", id=f"calendar-row-{week}"):
                    for day in range(DAYS):
                        yield Label("", id=f"calendar-cell-{week}-{day}")

    def on_mount(self) -> None:
        self._visible = self.period.anchor.replace(day=1)
        self.rebuild()

    # -- drawing -----------------------------------------------------------

    def rebuild(self) -> None:
        period = self.period
        # Follow the anchor when it *moves*, but leave a browsed month alone:
        # paging ahead with `,` and `.` to see where the bank holidays fall must
        # not be undone by the next redraw.
        if period.anchor != self._last_anchor:
            self._visible = period.anchor.replace(day=1)
            self._last_anchor = period.anchor
        grid = _month_grid(self._visible)
        ledgers = {
            item.date: item
            for item in self.services.ledger.days(grid[0], grid[-1], now=self.now)
        }
        today = self.now.date()

        self.query_one("#calendar-label", Label).update(
            f"{calendar.month_name[self._visible.month]} {self._visible.year}"
        )

        for index, when in enumerate(grid):
            week, column = divmod(index, DAYS)
            cell = self.query_one(f"#calendar-cell-{week}-{column}", Label)
            ledger = ledgers.get(when)
            cell.update(self._cell_text(when, ledger, period, today))
            cell.set_classes(" ".join(self._cell_classes(when, ledger, period, today)))

        for week in range(WEEKS):
            row = self.query_one(f"#calendar-row-{week}")
            covered = any(
                period.contains(grid[week * DAYS + column]) for column in range(DAYS)
            )
            row.set_class(
                covered and period.granularity is Granularity.WEEK, "in-period"
            )

        self.set_subtitle(period.label)

    def _cell_text(
        self,
        when: date,
        ledger: DayLedger | None,
        period: Period,
        today: date,
    ) -> Text:
        """A day number, underlined when it is today.

        Underline rather than another colour: today can coincide with a selected
        day, a booked day and the edge of the period, and a fourth colour on the
        same cell would make all four unreadable.
        """
        text = Text(f"{when.day:2d}")
        if when == today:
            text.stylize("underline")
        del ledger, period
        return text

    def _cell_classes(
        self,
        when: date,
        ledger: DayLedger | None,
        period: Period,
        today: date,
    ) -> list[str]:
        classes: list[str] = []
        if when.month != self._visible.month:
            classes.append("not-current-month")
        if when == today:
            classes.append("today")
        if when == period.anchor:
            classes.append("selected")
        if period.contains(when) and period.granularity is not Granularity.WEEK:
            classes.append("in-period")
        if ledger is not None:
            classes.append(KIND_CLASSES.get(ledger.kind, "day-working"))
            if ledger.absences:
                classes.append(f"absence-{ledger.absences[0].type.token}")
            elif ledger.segments:
                classes.append("day-worked")
        return classes

    # -- interaction -------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_month(-1 if event.button.id == "calendar-prev" else 1)

    def action_move(self, offset: int) -> None:
        self.post_message(DateSelected(self.period.anchor + timedelta(days=offset)))

    def action_month(self, offset: int) -> None:
        """Page the grid without moving the period.

        Browsing ahead to see where the bank holidays fall should not change what
        the records table is showing; ``enter`` is what commits a move.
        """
        self._visible = _add_month(self._visible, offset)
        self.rebuild()

    def action_select(self) -> None:
        self.post_message(DateSelected(self.period.anchor))


def _month_grid(first_of_month: date) -> list[date]:
    """Six weeks of dates covering the month, Monday first."""
    start = first_of_month - timedelta(days=first_of_month.weekday())
    return [start + timedelta(days=offset) for offset in range(WEEKS * DAYS)]


def _add_month(when: date, offset: int) -> date:
    total = when.year * 12 + when.month - 1 + offset
    return date(total // 12, total % 12 + 1, 1)
