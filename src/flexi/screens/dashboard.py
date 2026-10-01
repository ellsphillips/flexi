"""The dashboard: clock, balance, wallet, calendar and records.

The screen owns the period, the tick and the modals. Modules read the period and
redraw; they never move it, which is what makes the calendar and the records
table agree.

Redraw is scoped: a module declares which kinds of change it cares about, the
screen invalidates the ledger cache once, and only interested modules rebuild.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from types import MappingProxyType
from typing import ClassVar, Final, Unpack

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
from flexi.components.expandable import RowKind, row_ident
from flexi.components.jumper import JumpInfo
from flexi.components.modules.balance import BalanceModule
from flexi.components.modules.base import Module
from flexi.components.modules.clock import ClockModule
from flexi.components.modules.monthview import MonthView
from flexi.components.modules.records import BookHere, DeleteHere, RecordsModule
from flexi.components.modules.wallet import BookRequested, WalletModule
from flexi.components.options import ScreenOptions
from flexi.components.progress import TimeProgress
from flexi.config import CONFIG
from flexi.constants import AbsenceType, Granularity
from flexi.domain.balance import BalanceSummary, expected_for
from flexi.domain.format import clock as clock_time
from flexi.domain.format import hm, short_date
from flexi.domain.period import Period
from flexi.messages import DateSelected, Scope
from flexi.screens.modals import (
    AbsenceBooking,
    AbsenceModal,
    Adjustment,
    AdjustmentModal,
    ConfirmModal,
    Correction,
    CorrectionModal,
    CorrectionsModal,
    GoToDateModal,
)
from flexi.services.absence import snapshot_booking
from flexi.services.clock import ClockResult
from flexi.services.outcome import Outcome
from flexi.services.registry import (
    Services,
    adjust_balance,
    available_toil_days,
    invalidate_services,
)

__all__ = ("JUMP_TARGETS", "DashboardScreen", "with_time")

JUMP_TARGETS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "clock-module": "c",
        "balance-module": "b",
        "wallet-module": "w",
        "records-module": "r",
        "month-view": "p",
    }
)


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
        Binding(CONFIG.hotkeys.new_session, "correct", "Record work", show=True),
        Binding(CONFIG.hotkeys.corrections, "corrections", "Corrections", show=False),
        # Shifted, so they never collide with the records table's letters, and
        # bound on the screen so one keystroke books leave from anywhere.
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

    def __init__(self, services: Services, **kwargs: Unpack[ScreenOptions]) -> None:
        super().__init__(**kwargs)
        self._services = services
        self.period = Period.containing(
            wallclock.today(),
            CONFIG.defaults.period,
            year_start=services.settings.get_leave_year_start(),
            first_weekday=CONFIG.defaults.first_day_of_week,
        )
        self.now = wallclock.now()
        self._today = self.now.date()
        """The date the tick last saw, so it can tell when midnight passes."""
        self._tick: Timer | None = None
        self._shown: tuple[BalanceSummary, ...] = ()
        """The minutes the records table and the wallet were last drawn at."""

    # ---- composition ----

    def compose(self) -> ComposeResult:
        yield AppHeader()
        # Not docked: two widgets docked to the same edge land on the same row
        # and the later one wins. The header docks above and the footer below,
        # which leaves exactly one row for the rails to flow into.
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
        self._shown = self._shown_minutes()
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

    # ---- period ----

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

    # ---- redrawing ----

    def refresh_modules(self, scope: Scope) -> None:
        """Invalidate once, then redraw only the modules that care."""
        self.now = wallclock.now()
        if scope & Scope.SETTINGS:
            self.period = self.period.with_year_start(
                self._services.settings.get_leave_year_start()
            )
            self._sync_header()
        if scope & (Scope.CLOCK | Scope.ABSENCE | Scope.SETTINGS):
            invalidate_services(self._services)
        for module in self.query(Module):
            module.rebuild_if(scope)
        self._shown = self._shown_minutes()
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
            header.context = self.period.label

    # ---- the live tick ----

    def _start_tick_if_open(self) -> None:
        """Run a one-second timer only while a session is open."""
        open_now = self._services.ledger.day(wallclock.today()).is_open
        if open_now and self._tick is None:
            self._tick = self.set_interval(CONFIG.defaults.tick_seconds, self._on_tick)
        elif not open_now and self._tick is not None:
            self._tick.stop()
            self._tick = None

    def _on_tick(self) -> None:
        """A second passed. Redraw the two readouts that measure elapsed time.

        Everything else that moves with the clock prints whole minutes, and a
        year of records takes some fifty milliseconds to build, so it waits
        for `TIME`: a figure on screen reaching its next minute.

        No `invalidate()`: nothing was written, and `LedgerService.days` always
        rebuilds today, which an open session lengthens a minute at a time.
        Clearing the memo would throw away every other day in the period with it.
        """
        self.now = wallclock.now()
        if self.now.date() != self._today:
            self._turn_the_day()
            return
        for module in (ClockModule, BalanceModule):
            for widget in self.query(module):
                widget.rebuild()
        shown = self._shown_minutes()
        if shown != self._shown:
            self._shown = shown
            for panel in self.query(Module):
                panel.rebuild_if(Scope.TIME)
        self._refresh_progress()

    def _turn_the_day(self) -> None:
        """Midnight passed under an open dashboard.

        A session left running is closed at the auto-close time, as the next
        launch or `/` would close it; until then the balance counts it to
        midnight. A period that showed the old date moves to the new one, and
        the tick stops if nothing is left on the clock.
        """
        was, self._today = self._today, self.now.date()
        # The same sweep launch and `/` run: whatever reports what one closed
        # belongs beside each call.
        self._services.clock.sweep()
        if self.period.contains(was):
            self.period = self.period.go_to(self._today)
        self.refresh_modules(Scope.ALL)
        self._start_tick_if_open()

    def _shown_minutes(self) -> tuple[BalanceSummary, ...]:
        """Today, the period and the balance, in the whole minutes they print.

        Punches count from the minute they show and an open session to the
        minute on the clock, so these turn over as the wall clock's minute does.
        """
        ledger = self._services.ledger
        today = self.now.date()
        return (
            ledger.summary(today, today, now=self.now).as_shown(),
            ledger.summary(self.period.start, self.period.end, now=self.now).as_shown(),
            ledger.balance(today, now=self.now).as_shown(),
        )

    def on_unmount(self) -> None:
        if self._tick is not None:
            self._tick.stop()

    # ---- clocking ----

    def sweep(self) -> bool:
        """Close work left running on an earlier day, and say so.

        Launch and `/` sweep through here, so neither closes a session
        unannounced: the auto-close time can be hours after the person left,
        and only they know when. `ClockService.clock_in` sweeps again, but
        only after `/` has, so it finds nothing left to close. Answers whether
        one was closed; redrawing is the caller's.
        """
        closed = self._services.clock.sweep()
        for result in closed:
            self.notify(
                f"{result.message}. If you left earlier: open the day in Records, "
                "press x on the session, then n.",
                severity="warning",
                timeout=10,
                markup=False,
            )
        return bool(closed)

    def on_clock_module_toggle(self, event: ClockModule.Toggle) -> None:
        event.stop()
        self.toggle_clock()

    def toggle_clock(self) -> tuple[str, Tone]:
        """Clock in, or clock out. It never asks.

        Clock events are immutable and a second `/` opens a new session, so a
        mistaken press costs one visible break, and the status bar is the
        receipt. That receipt is returned as well as shown: `/` is bound on the
        application, and from Leave or Insights this screen's footer sits under
        the one being read.
        """
        clock = self._services.clock
        # A session left running overnight is drawn as closed the moment the
        # date turns, and `sweep` makes that true in the database. It runs
        # first, or the morning's `/` closes yesterday at this morning's time.
        # A press that closed one stops there: past midnight it is as likely
        # meant as a clock-out, and clocking in would open a session nobody
        # is working.
        if self.sweep():
            receipt = (
                "Closed the session left running; press again to clock in",
                Tone.WARN,
            )
            self.status(*receipt)
            self.refresh_modules(Scope.CLOCK)
            self._start_tick_if_open()
            return receipt
        if clock.is_clocked_in():
            return self._report(clock.clock_out())
        return self._report(clock.clock_in())

    # ---- absence ----

    def on_book_requested(self, event: BookRequested) -> None:
        event.stop()
        self.open_absence_modal(self.period.anchor, event.kind)

    def on_book_here(self, event: BookHere) -> None:
        event.stop()
        when = date.fromisoformat(event.iso) if event.iso else self.period.anchor
        self.open_absence_modal(when, AbsenceType.ANNUAL)

    def action_correct(self) -> None:
        """Record work on the selected day that was not clocked at the time.

        Opens on the day under the records cursor, and on the period anchor
        when the table does not hold the cursor.
        """

        def record(correction: Correction | None) -> None:
            if correction is None:
                return
            self._report(
                self._services.clock.correct(
                    correction.day, correction.opened, correction.closed
                ),
                scope=Scope.CLOCK,
            )

        day = self._selected_day()
        ledger = self._services.ledger.day(day)
        self.app.push_screen(
            CorrectionModal(
                day,
                tracking_since=self._services.settings.resolved().tracking_since,
                # Recorded work tracks the day whatever `tracking_since` says,
                # so ask what it expects once tracked, not while it is empty.
                expected=expected_for(
                    ledger.contracted,
                    is_tracked=True,
                    is_working_day=ledger.is_working_day,
                    is_holiday=ledger.is_holiday,
                    absences=ledger.absences,
                ),
            ),
            callback=record,
        )

    def _selected_day(self) -> date:
        """The day the records cursor is on, or the anchor if it is elsewhere."""
        for records in self.query(RecordsModule):
            iso = records.selected_date() if records.has_focus_within else None
            if iso is not None:
                return date.fromisoformat(iso)
        return self.period.anchor

    def action_corrections(self) -> None:
        """Read back every correction in the period, in one list."""
        self.app.push_screen(
            CorrectionsModal(
                self.period.label,
                self._services.clock.corrections_between(
                    self.period.start, self.period.end
                ),
            )
        )

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
                available_toil_days=available_toil_days(self._services),
            )
            self._report(result, scope=Scope.ABSENCE)

        self.app.push_screen(
            AbsenceModal(
                when,
                kind,
                remaining=self._services.absence.get_remaining_annual_leave(when),
                toil_days=available_toil_days(self._services),
            ),
            callback=book,
        )

    def on_delete_here(self, event: DeleteHere) -> None:
        event.stop()
        if event.key is None:
            return
        absence = row_ident(RowKind.ABSENCE, event.key)
        session = row_ident(RowKind.SESSION, event.key)
        if absence is not None:
            self._delete_absence(int(absence))
        elif session is not None and session.isdigit():
            # Not a break, which is keyed after the session before it.
            self._void_session(int(session))
        elif event.key.startswith((RowKind.DAY, RowKind.SESSION)):
            self.status("Select a session to void or a booking to remove", Tone.WARN)

    def _void_session(self, session_id: int) -> None:
        """Ask before voiding a session, naming it and the way back."""
        segment = self._services.clock.segment(session_id)
        if segment is None:
            self.status("That session has already gone", Tone.WARN)
            return
        if segment.end is None:
            self.status(
                "Clock out first; a running session cannot be voided", Tone.WARN
            )
            return
        day = segment.start.date()
        question = (
            f"Void {clock_time(segment.start)} → {clock_time(segment.end)} on "
            f"{short_date(day)} ({hm(segment.duration(self.now))})? It stops "
            "counting; the clock record is kept. Add the real hours with n."
        )
        # A settlement is a fixed amount, sized to zero the balance at its date,
        # and `first_line_after` is exclusive: a line drawn on the day covers it.
        _, year_end = self._services.absence.leave_year_bounds(day)
        line = self._services.adjustments.first_line_after(
            day - timedelta(days=1), year_end
        )
        if line is not None:
            question += (
                " The balance you settled to zero on or after this day will no "
                "longer read zero."
            )

        def confirm(answer: bool | None) -> None:  # noqa: FBT001 - Textual passes a dismissal result positionally
            if answer:
                self._report(self._services.clock.void(session_id))

        self.app.push_screen(
            ConfirmModal(question, title="Void session"), callback=confirm
        )

    def _delete_absence(self, absence_id: int) -> None:
        found = self._services.absence.by_id(absence_id)
        if found is None:
            self.status("That booking has already gone", Tone.WARN)
            return
        booking = snapshot_booking(found)

        def confirm(answer: bool | None) -> None:  # noqa: FBT001 - Textual passes a dismissal result positionally
            if answer:
                self._report(
                    self._services.absence.remove_booking(booking),
                    scope=Scope.ABSENCE,
                )

        self.app.push_screen(
            ConfirmModal(
                f"Remove {booking.absence_type.phrase} "
                f"from {short_date(booking.date)}?",
                title="Remove booking",
            ),
            callback=confirm,
        )

    # ---- the balance ----

    def action_adjust_balance(self) -> None:
        """Bring a balance in, or correct it, from today.

        Written by the registry function `flexi balance adjust` calls, so a date
        the command line would refuse is refused here as well.
        """

        def adjust(adjustment: Adjustment | None) -> None:
            if adjustment is None:
                return
            self._report(
                adjust_balance(
                    self._services,
                    adjustment.amount,
                    adjustment.reason,
                    adjustment.when,
                ),
                # It moves every figure a corrected session moves.
                scope=Scope.CLOCK,
            )

        self.app.push_screen(AdjustmentModal(wallclock.today()), callback=adjust)

    # ---- reporting ----

    def _report(self, result: Outcome, scope: Scope = Scope.CLOCK) -> tuple[str, Tone]:
        """Put a service result on the status bar, and redraw if it wrote.

        Returns what was said, for a caller that has a second footer to say it
        on.
        """
        success = result.success
        if success and result.warning:
            receipt = (result.warning, Tone.WARN)
        else:
            receipt = (
                with_time(result.message, result),
                Tone.OK if success else Tone.ERR,
            )
        self.status(*receipt)
        if success:
            self.refresh_modules(scope)
            self._start_tick_if_open()
        return receipt

    def status(self, message: str, tone: Tone = Tone.NEUTRAL) -> None:
        """Say what just happened."""
        for footer in self.query(AppFooter):
            footer.set_status(message, tone)


def with_time(message: str, result: Outcome) -> str:
    """Stamp a clock result with the moment it recorded.

    The result carries the time, so rewording a service's message cannot drop
    it.
    """
    if not isinstance(result, ClockResult) or result.at is None:
        return message
    return f"{message} at {clock_time(result.at)}"
