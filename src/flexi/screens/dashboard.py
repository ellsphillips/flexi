"""The dashboard: clock, balance, wallet, calendar and records.

The screen owns the period, the tick and the modals. Modules read the period and
redraw; they never move it, which is what makes the calendar and the records
table agree.

Redraw is scoped: a module declares which kinds of change it cares about, the
screen invalidates the ledger cache once, and only interested modules rebuild.
"""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.geometry import Offset
from textual.screen import Screen
from textual.timer import Timer

from flexi import wallclock
from flexi.components.chrome import AppFooter, AppHeader
from flexi.components.common import TINY_COLUMNS, Tone, mark_width
from flexi.components.expandable import ABSENCE, DAY, SESSION
from flexi.components.jumper import JumpInfo
from flexi.components.modules.balance import BalanceModule
from flexi.components.modules.base import Module
from flexi.components.modules.clock import ClockModule
from flexi.components.modules.monthview import MonthView
from flexi.components.modules.records import BookHere, DeleteHere, RecordsModule
from flexi.components.modules.wallet import BookRequested, WalletModule
from flexi.components.progress import TimeProgress
from flexi.config import CONFIG
from flexi.constants import AbsenceType, Granularity
from flexi.domain.format import clock as clock_time
from flexi.domain.format import short_date
from flexi.domain.period import Period
from flexi.messages import DateSelected, Scope
from flexi.screens.modals import (
    AbsenceBooking,
    AbsenceModal,
    ConfirmModal,
    GoToDateModal,
)
from flexi.services.clock import ClockResult
from flexi.services.outcome import Outcome
from flexi.services.registry import Services

JUMP_TARGETS = {
    "clock-module": "c",
    "balance-module": "b",
    "wallet-module": "w",
    "records-module": "r",
    "month-view": "p",
}


class DashboardScreen(Screen[None]):
    """Everything you need twice a day, on one screen."""

    HELP_LABEL = "Dashboard"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(CONFIG.hotkeys.today, "today", "Today", show=True),
        Binding(CONFIG.hotkeys.period_prev, "shift(-1)", "Previous", show=False),
        Binding(CONFIG.hotkeys.period_next, "shift(1)", "Next", show=False),
        Binding(CONFIG.hotkeys.period_day, "zoom('day')", "Day", show=False),
        Binding(CONFIG.hotkeys.period_week, "zoom('week')", "Week", show=False),
        Binding(CONFIG.hotkeys.period_month, "zoom('month')", "Month", show=False),
        Binding(CONFIG.hotkeys.period_year, "zoom('year')", "Year", show=False),
        Binding(CONFIG.hotkeys.period_cycle, "cycle", "Period", show=True),
        Binding(CONFIG.hotkeys.go_to_date, "go_to_date", "Go to date", show=False),
        # Shifted, so they never collide with the record table's letters, and on
        # the screen rather than the wallet so one keystroke books leave from
        # anywhere on the dashboard.
        Binding(
            CONFIG.hotkeys.book_annual, "book('annual')", "Annual leave", show=False
        ),
        Binding(CONFIG.hotkeys.book_sick, "book('sick')", "Sickness", show=False),
        Binding(CONFIG.hotkeys.book_toil, "book('flexi')", "TOIL day", show=False),
        Binding(
            CONFIG.hotkeys.book_unpaid, "book('unpaid')", "Unpaid leave", show=False
        ),
        Binding(
            CONFIG.hotkeys.book_other, "book('other')", "Other absence", show=False
        ),
    ]

    def action_book(self, kind: str) -> None:
        """Open the booking modal, pre-filled with one type."""
        self.open_absence_modal(self.period.anchor, AbsenceType(kind))

    def __init__(self, services: Services, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._services = services
        self.period = Period.containing(
            wallclock.today(),
            CONFIG.defaults.period,
            year_start=services.settings.get_leave_year_start(),
            first_weekday=CONFIG.defaults.first_day_of_week,
        )
        self.now = wallclock.now()
        self._tick: Timer | None = None

    # -- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield AppHeader()
        # Not docked. Two widgets docked to the same edge both land on the same
        # row and the later one wins, so the rails simply flow: the header is
        # docked above them and the footer below, which leaves exactly one row.
        yield TimeProgress(id="time-progress")
        with Horizontal(id="dashboard-body"):
            with VerticalScroll(id="dashboard-controls"):
                yield ClockModule()
                yield BalanceModule()
                yield WalletModule()
                yield MonthView()
            with Vertical(id="dashboard-records"):
                yield RecordsModule()
        yield AppFooter()

    def on_mount(self) -> None:
        self._sync_header()
        self._refresh_progress()
        self._start_tick_if_open()

    def on_resize(self) -> None:
        mark_width(self, self.size.width)
        self._refresh_progress()

    def jump_targets(self) -> dict[str, str]:
        """The panels, by widget id."""
        return dict(JUMP_TARGETS)

    def jump_overlays(self) -> dict[Offset, JumpInfo]:
        """The extra targets that are not widgets: the records table's day rows."""
        try:
            records = self.query_one(RecordsModule)
        except NoMatches:
            return {}
        return records.jump_row_targets()

    # -- period ------------------------------------------------------------

    def set_period(self, period: Period) -> None:
        """Move the temporal view and redraw everything that depends on it."""
        self.period = period
        self._sync_header()
        self.refresh_modules(Scope.PERIOD)

    def action_today(self) -> None:
        """Return to now, keeping the width the user chose."""
        self.set_period(self.period.go_to(wallclock.today()))

    def action_shift(self, count: int) -> None:
        self.set_period(self.period.shift(count))

    def action_zoom(self, granularity: str) -> None:
        self.set_period(self.period.zoom(Granularity(granularity)))

    def action_cycle(self) -> None:
        self.set_period(self.period.zoom(self.period.granularity.next()))

    def action_go_to_date(self) -> None:
        def apply(when: date | None) -> None:
            if when is not None:
                self.set_period(self.period.go_to(when))

        self.app.push_screen(GoToDateModal(self.period.anchor), callback=apply)

    def on_date_selected(self, event: DateSelected) -> None:
        event.stop()
        self.set_period(self.period.go_to(event.date))

    # -- redrawing ---------------------------------------------------------

    def refresh_modules(self, scope: Scope) -> None:
        """Invalidate once, then redraw only the modules that care."""
        self.now = wallclock.now()
        if scope & (Scope.CLOCK | Scope.ABSENCE | Scope.SETTINGS):
            self._services.invalidate()
        for module in self.query(Module):
            module.rebuild_if(scope)
        self._refresh_progress()

    def _refresh_progress(self) -> None:
        """The two rails under the header: today, and the shown period."""
        today = self.now.date()
        day = self._services.ledger.day(today, now=self.now)
        period = self._services.ledger.summary(
            self.period.start, self.period.end, now=self.now
        )
        for rails in self.query(TimeProgress):
            rails.show(
                day_done=day.worked,
                day_total=day.expected,
                period_label=self.period.granularity.label,
                period_done=period.worked,
                period_total=period.expected,
                compact=self.size.width < TINY_COLUMNS,
            )

    def _sync_header(self) -> None:
        for header in self.query(AppHeader):
            header.context = f"{short_date(wallclock.today())} · {self.period.label}"

    # -- the live tick -----------------------------------------------------

    def _start_tick_if_open(self) -> None:
        """Run a one-second timer only while a session is open.

        A minute-grained readout would jump in sixty-second steps and look like a
        hung process; a timer that ran when nothing was moving would redraw the
        whole dashboard once a second for no reason.
        """
        open_now = self._services.ledger.day(wallclock.today()).is_open
        if open_now and self._tick is None:
            self._tick = self.set_interval(CONFIG.defaults.tick_seconds, self._on_tick)
        elif not open_now and self._tick is not None:
            self._tick.stop()
            self._tick = None

    def _on_tick(self) -> None:
        """A second passed. Redraw the two readouts that measure elapsed time.

        No `invalidate()`: nothing was written, and `LedgerService.days`
        already rebuilds *today* on every call for exactly this reason -- an
        open session's length changes every second, so caching it would freeze
        the live readout. Clearing the whole memo threw away every other day in
        the period as well, so a month view re-derived thirty-one day ledgers a
        second to refresh the one the memo was never keeping.
        """
        self.now = wallclock.now()
        for module in (ClockModule, BalanceModule):
            for widget in self.query(module):
                widget.rebuild()
        self._refresh_progress()

    def on_unmount(self) -> None:
        if self._tick is not None:
            self._tick.stop()

    # -- clocking ----------------------------------------------------------

    def on_clock_module_toggle(self, event: ClockModule.Toggle) -> None:
        event.stop()
        self.toggle_clock()

    def toggle_clock(self) -> None:
        """Clock in, or clock out. It never asks.

        An earlier draft confirmed an early clock-out and fired at lunchtime every day,
        because clocking out for lunch is the normal thing this application is for.
        Clock events are immutable and a second `/` opens a new session, so a mistaken
        press costs one visible break; the status bar is the receipt.
        """
        clock = self._services.clock
        if clock.is_clocked_in():
            self._report(clock.clock_out())
        else:
            self._report(clock.clock_in())

    # -- absence -----------------------------------------------------------

    def on_book_requested(self, event: BookRequested) -> None:
        event.stop()
        self.open_absence_modal(self.period.anchor, event.kind)

    def on_book_here(self, event: BookHere) -> None:
        event.stop()
        when = date.fromisoformat(event.iso) if event.iso else self.period.anchor
        self.open_absence_modal(when, AbsenceType.ANNUAL)

    def open_absence_modal(self, when: date, kind: AbsenceType) -> None:
        """Ask what to book, pre-filled, with the allowances in view."""

        def book(booking: AbsenceBooking | None) -> None:
            if booking is None:
                return
            result = self._services.absence.book(
                booking.when,
                booking.kind,
                booking.portion,
                note=booking.note,
                available_toil_days=self._services.toil_days(),
            )
            self._report(result, scope=Scope.ABSENCE)

        self.app.push_screen(
            AbsenceModal(
                when,
                kind,
                remaining=self._services.absence.get_remaining_annual_leave(),
                toil_days=self._services.toil_days(),
            ),
            callback=book,
        )

    def on_delete_here(self, event: DeleteHere) -> None:
        event.stop()
        if event.key is None:
            return
        if event.key.startswith(ABSENCE):
            self._delete_absence(int(event.key[len(ABSENCE) :]))
        elif event.key.startswith((DAY, SESSION)):
            self.status("Deleting sessions is not implemented yet", Tone.WARN)

    def _delete_absence(self, absence_id: int) -> None:
        found = self._services.absence.by_id(absence_id)
        if found is None:
            self.status("That booking has already gone", Tone.WARN)
            return
        when, portion = found.date, found.portion

        def confirm(answer: bool | None) -> None:
            if answer:
                self._report(
                    self._services.absence.remove(when, portion), scope=Scope.ABSENCE
                )

        self.app.push_screen(
            ConfirmModal(
                f"Remove {found.absence_type.phrase} from {short_date(when)}?",
                title="Remove booking",
            ),
            callback=confirm,
        )

    # -- reporting ---------------------------------------------------------

    def _report(self, result: Outcome, scope: Scope = Scope.CLOCK) -> None:
        """Put a service result on the status bar, and redraw if it wrote."""
        success = result.success
        message = _with_time(result.message, result)
        if success and result.warning:
            self.status(result.warning, Tone.WARN)
        else:
            self.status(message, Tone.OK if success else Tone.ERR)
        if success:
            self.refresh_modules(scope)
            self._start_tick_if_open()

    def status(self, message: str, tone: Tone = Tone.NEUTRAL) -> None:
        """Say what just happened."""
        for footer in self.query(AppFooter):
            footer.set_status(message, tone)


def _with_time(message: str, result: Outcome) -> str:
    """Stamp a clock result with the moment it recorded.

    "Clocked out" is a fact about the past tense; "Clocked out at 12:04" is a
    fact somebody can check against the clock on their wall, which is what makes
    a mistaken keystroke visible the moment it happens.

    The result says whether it recorded one. It used to be inferred, by reaching
    past the `Outcome` protocol with two `getattr`s for a `ClockEvent` and then
    asking whether the message began with the word "Clocked" -- so the sentence
    a service wrote for a status bar was load-bearing, and rewording it would
    have dropped the time with nothing to say so.
    """
    if not isinstance(result, ClockResult) or result.at is None:
        return message
    return f"{message} at {clock_time(result.at)}"
