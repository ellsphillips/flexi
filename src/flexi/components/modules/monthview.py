"""Where you are in time, and how to get somewhere else.

Three separate facts share this grid — today, the selected day, and the extent
of the period — and giving each a colour makes a mess nobody can read. So each
gets a different device: the period tints the ground, the selection reverses a
cell, today is underlined. All three can be true of one cell and it still reads,
which leaves colour free to carry the day type.
"""

from __future__ import annotations

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
from flexi.domain.dates import DAYS_IN_WEEK, add_months, week_start
from flexi.domain.format import month_title
from flexi.domain.ledger import DayLedger
from flexi.domain.period import Granularity, Period
from flexi.domain.stitch import weekday_initials
from flexi.messages import DateSelected, Scope

WEEKS = 6

KIND_CLASSES: dict[DayKind, str] = {
    DayKind.HOLIDAY: "day-holiday",
    DayKind.ABSENT: "day-absent",
    DayKind.PARTIAL: "day-partial",
    DayKind.WEEKEND: "day-weekend",
    DayKind.WORKING: "day-working",
}


class MonthView(Module):
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
            id="month-view",
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
                for initial in weekday_initials(CONFIG.defaults.first_day_of_week):
                    yield Label(initial)
            for week in range(WEEKS):
                with Horizontal(classes="calendar-row", id=f"calendar-row-{week}"):
                    for day in range(DAYS_IN_WEEK):
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
        grid = _month_grid(self._visible, period.first_weekday)
        ledgers = {
            item.date: item
            for item in self.services.ledger.days(grid[0], grid[-1], now=self.now)
        }
        today = self.now.date()

        self.query_one("#calendar-label", Label).update(
            month_title(self._visible.year, self._visible.month)
        )

        for index, when in enumerate(grid):
            week, column = divmod(index, DAYS_IN_WEEK)
            cell = self.query_one(f"#calendar-cell-{week}-{column}", Label)
            cell.update(self._cell_text(when, today))
            # Written only when they differ. `set_classes` reapplies the whole
            # stylesheet to the tree whether or not anything changed, and the
            # calendar has forty-two cells of which a redraw typically moves
            # one: stepping a day was forty-two full restyles, and mounting the
            # dashboard was forty-two more.
            classes = set(self._cell_classes(when, ledgers.get(when), period, today))
            if classes != set(cell.classes):
                cell.set_classes(classes)

        for week in range(WEEKS):
            row = self.query_one(f"#calendar-row-{week}")
            covered = any(
                period.contains(grid[week * DAYS_IN_WEEK + column])
                for column in range(DAYS_IN_WEEK)
            )
            row.set_class(
                covered and period.granularity is Granularity.WEEK, "in-period"
            )

        self.set_subtitle(period.label)

    def _cell_text(self, when: date, today: date) -> Text:
        """A day number, underlined when it is today.

        Underline rather than another colour: today can coincide with a selected
        day, a booked day and the edge of the period, and a fourth colour on the
        same cell would make all four unreadable.
        """
        text = Text(f"{when.day:2d}")
        if when == today:
            text.stylize("underline")
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
        self._visible = add_months(self._visible, offset).replace(day=1)
        self.rebuild()

    def action_select(self) -> None:
        self.post_message(DateSelected(self.period.anchor))


def _month_grid(first_of_month: date, first_weekday: int = 0) -> list[date]:
    """Six weeks of dates covering the month, starting on the configured day.

    It always started on Monday, while the period the same widget tints came
    from `CONFIG.defaults.first_day_of_week`. Set the week to start on Sunday
    and one week of the period straddled two rows of the grid, so fourteen days
    were highlighted as "this week" under headings that said Monday.
    """
    start = week_start(first_of_month, first_weekday)
    return [start + timedelta(days=offset) for offset in range(WEEKS * DAYS_IN_WEEK)]
