"""The leave screen: a scrolling leave year you book directly on.

The screen owns the selection and the writes; the calendar owns the surface and
the cursor. That split keeps booking to one keystroke: `A` reads the selection,
calls the service and reports what happened, with no modal in between. The
modal (`e`) covers what a keystroke cannot say, such as annual leave with a
note.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from datetime import date
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static

from flexi import wallclock
from flexi.components.allowance import paint_allowance
from flexi.components.chrome import AppFooter, AppHeader
from flexi.components.common import Gauge, Tone, mark_width
from flexi.components.yearcalendar import YearCalendar, legend
from flexi.config import CONFIG
from flexi.constants import AbsenceType, Granularity, Portion, Verdict
from flexi.domain.format import days as fmt_days
from flexi.domain.format import delta, plural, stamp
from flexi.domain.ledger import DayLedger
from flexi.domain.period import Period
from flexi.domain.stitch import Selection
from flexi.messages import Scope
from flexi.screens.modals import (
    AbsenceBooking,
    AbsenceModal,
    ConfirmModal,
    GoToDateModal,
)
from flexi.services.absence import AbsencePlan, RemovalPlan
from flexi.services.registry import (
    Services,
    available_toil_days,
    invalidate_services,
)

__all__ = (
    "PORTION_CYCLE",
    "REMOVE_THRESHOLD",
    "SIDEBAR",
    "LeaveScreen",
    "nothing_doing",
    "preview",
    "working_day",
)

SIDEBAR: tuple[AbsenceType, ...] = (
    AbsenceType.ANNUAL,
    AbsenceType.FLEXI,
    AbsenceType.SICK,
)
"""The allowances the planner has room for beside a year calendar.

Three of the five the dashboard's wallet shows: the ones a booking decision
turns on. Named apart from that module's ``TRACKED``, which is a longer tuple.
"""

PORTION_CYCLE: tuple[Portion, ...] = (Portion.FULL, Portion.AM, Portion.PM)

REMOVE_THRESHOLD = 3
"""Clearing more than this many bookings asks for confirmation first.

At or below the threshold, rebooking is faster than confirming.
"""


class LeaveScreen(Screen[None]):
    """Book, change and remove leave across a whole year."""

    HELP_LABEL = "Leave"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(CONFIG.hotkeys.book_annual, "book('annual')", "Annual", show=True),
        Binding(CONFIG.hotkeys.book_sick, "book('sick')", "Sick", show=True),
        Binding(CONFIG.hotkeys.book_toil, "book('flexi')", "TOIL", show=True),
        Binding(CONFIG.hotkeys.book_unpaid, "book('unpaid')", "Unpaid", show=False),
        Binding(CONFIG.hotkeys.book_other, "book('other')", "Other", show=False),
        Binding(CONFIG.hotkeys.delete, "remove", "Remove", show=True),
        Binding("space", "cycle_portion", "Half day", show=True),
        Binding(CONFIG.hotkeys.edit, "edit", "Edit", show=False),
        Binding(CONFIG.hotkeys.today, "today", "Today", show=False),
        Binding(CONFIG.hotkeys.go_to_date, "go_to_date", "Go to date", show=False),
        Binding("escape", "back", "Back", show=False),
    ]

    def __init__(
        self,
        services: Services,
        anchor: date | None = None,
        *,
        name: str | None = None,
        id: str | None = None,  # noqa: A002 - Textual's parameter name
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._services = services
        self.now = wallclock.now()
        self.portion = Portion.FULL
        self.period = Period.containing(
            anchor or wallclock.today(),
            Granularity.YEAR,
            year_start=services.settings.get_leave_year_start(),
            first_weekday=CONFIG.defaults.first_day_of_week,
        )

    # --- composition ------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield AppHeader()
        with Horizontal(id="leave-body"):
            yield YearCalendar(id="leave-calendar", classes="module")
            with Vertical(id="leave-rail"):
                yield Static("", id="leave-wallet-line", classes="caption")
                with Vertical(id="leave-wallet", classes="module"):
                    for kind in SIDEBAR:
                        yield Gauge(kind.short, id=f"leave-gauge-{kind.token}")
                with Vertical(id="leave-selection", classes="module"):
                    yield Static("", id="leave-selection-label", classes="headline")
                    yield Static("", id="leave-selection-detail", classes="caption")
                    yield Static("", id="leave-selection-booked")
                with Vertical(id="leave-legend", classes="module"):
                    yield Static("", id="leave-legend-body")
        yield AppFooter()

    def on_mount(self) -> None:
        for header in self.query(AppHeader):
            header.set_active("leave")
        self.query_one(YearCalendar).border_title = "Leave"
        self.query_one("#leave-wallet", Vertical).border_title = "Wallet"
        self.query_one("#leave-selection", Vertical).border_title = "Selected"
        self.query_one("#leave-legend", Vertical).border_title = "Book"
        self.query_one("#leave-legend-body", Static).update(legend())
        self.rebuild()
        calendar = self.query_one(YearCalendar)
        if not self.period.contains(calendar.selection.head):
            # The cursor starts on today, which is off this grid when the
            # dashboard was browsing another leave year, and booking keys act
            # on the cursor.
            calendar.go_to(self.period.anchor)
        calendar.focus()
        # The calendar has no size until the first layout, so a scroll asked
        # for now clamps to the top.
        self.call_after_refresh(calendar.scroll_to_day, calendar.selection.head)

    def on_resize(self) -> None:
        mark_width(self, self.size.width)
        # The `-narrow` class is set here, after the first draw, and the
        # selection line is drawn differently under it.
        if self.is_mounted:
            self._draw_selection()

    def jump_targets(self) -> dict[str, str]:
        return {
            "leave-calendar": "c",
            "leave-wallet": "w",
            "leave-selection": "s",
            "leave-legend": "b",
        }

    # --- drawing ----------------------------------------------------------

    @property
    def calendar(self) -> YearCalendar:
        return self.query_one(YearCalendar)

    @property
    def selection(self) -> Selection:
        return self.calendar.selection

    def rebuild(self) -> None:
        """Reload the year and redraw everything that depends on it."""
        self.now = wallclock.now()
        start, end = self.period.start, self.period.end
        ledgers = {
            item.date: item
            for item in self._services.ledger.days(start, end, now=self.now)
        }
        calendar = self.calendar
        calendar.show(
            start,
            end,
            ledgers,
            today=self.now.date(),
            first_weekday=CONFIG.defaults.first_day_of_week,
        )
        calendar.scroll_to_day(calendar.selection.head)
        calendar.border_subtitle = self._booked_subtitle()
        self._draw_wallet()
        self._draw_selection()
        for header in self.query(AppHeader):
            header.context = self.period.label

    def _booked_subtitle(self) -> str:
        """How much of the year is already spoken for."""
        booked = self._services.absence.in_range(self.period.start, self.period.end)
        total = sum(row.portion.days for row in booked)
        if not total:
            return "nothing booked"
        return f"{fmt_days(total)} {plural(total, 'day')} booked"

    def _draw_wallet(self) -> None:
        data = self._services.wallet.compute(
            self.period.start, self.period.end, today=self.now.date(), now=self.now
        )
        annual = data.allowance(AbsenceType.ANNUAL)
        for kind in SIDEBAR:
            allowance = data.allowance(kind)
            paint_allowance(
                self.query_one(f"#leave-gauge-{allowance.token}", Gauge),
                allowance,
                data,
            )

        left = "—" if annual.remaining is None else f"{fmt_days(annual.remaining)} left"
        self.query_one("#leave-wallet-line", Static).update(
            f"ANNUAL {left} · TOIL {delta(data.balance.delta)}"
        )
        start, end = data.leave_year
        self.query_one(
            "#leave-wallet", Vertical
        ).border_subtitle = f"{stamp(start, '%-d %b %y')}–{stamp(end, '%-d %b %y')}"

    def _draw_selection(self) -> None:
        selection = self.selection
        # Once for the whole selection: `is_working_day` reads the settings
        # row, and a selection can span a month.
        pattern = set(self._services.settings.get_working_day_indices())
        ledgers = self.calendar.ledgers
        working = sum(
            1 for when in selection.days() if working_day(when, ledgers, pattern)
        )
        booked = self._services.absence.in_range(selection.start, selection.end)

        self.query_one("#leave-selection-label", Static).update(selection.label())
        count = f"{working} {plural(working, 'working day')}"
        portion = (
            "" if self.portion is Portion.FULL else f" · {self.portion.label.lower()}s"
        )
        self.query_one("#leave-selection-detail", Static).update(f"{count}{portion}")

        if not booked:
            body = "Nothing booked"
        elif len(booked) == 1:
            body = booked[0].absence_type.label + (
                ""
                if booked[0].portion is Portion.FULL
                else f" ({booked[0].portion.label.lower()})"
            )
        else:
            kinds: defaultdict[str, float] = defaultdict(float)
            for row in booked:
                kinds[row.absence_type.label] += row.portion.days
            body = " · ".join(
                f"{fmt_days(days)}d {label.lower()}" for label, days in kinds.items()
            )
        narrow = self.has_class("-narrow")
        if narrow:
            # One line instead of three: on a narrow terminal every row the
            # rail keeps costs a row of calendar.
            body = f"{selection.label()} · {count}{portion} · {body}"
        self.query_one("#leave-selection-booked", Static).update(body)

    def on_year_calendar_selection_changed(
        self, event: YearCalendar.SelectionChanged
    ) -> None:
        event.stop()
        if not self.period.contains(event.selection.head):
            self.period = self.period.go_to(event.selection.head)
            self.rebuild()
            self.calendar.scroll_to_day(event.selection.head)
            return
        self._draw_selection()

    # --- booking ----------------------------------------------------------

    def action_cycle_portion(self) -> None:
        """Full, morning, afternoon.

        Cycled before booking, so a half day costs one keystroke and the common
        full day costs none.
        """
        index = (PORTION_CYCLE.index(self.portion) + 1) % len(PORTION_CYCLE)
        self.portion = PORTION_CYCLE[index]
        self._draw_selection()
        self.status(f"Booking {self.portion.label.lower()}s", Tone.ACCENT)

    def action_book(self, kind: str) -> None:
        """Book the selection, asking first when there is something to ask about.

        Every span is planned, previewed and then committed, so a refused
        twelfth day is said before the other eleven are written. A single day
        books on the keystroke: it is one row, and `x` removes it.
        """
        absence_type = AbsenceType(kind)
        if absence_type.requires_note:
            self.action_edit(absence_type)
            return

        selection = self.selection
        self._offer(
            self._services.absence.plan(
                selection.start,
                selection.end,
                absence_type,
                self.portion,
                available_toil_days=available_toil_days(self._services),
            )
        )

    def _offer(self, plan: AbsencePlan) -> None:
        """Write a day, ask about a span, or say why there is nothing to write."""
        if plan.is_empty:
            self._after_write(nothing_doing(plan), ok=False)
            return

        if plan.start == plan.end:
            self._commit(plan)
            return

        def confirm(answer: bool | None) -> None:  # noqa: FBT001 - Textual passes a dismissal result positionally
            if answer:
                self._commit(plan)

        self.app.push_screen(
            ConfirmModal(
                preview(plan),
                title=f"Book {plan.absence_type.phrase}?",
            ),
            callback=confirm,
        )

    def _commit(self, plan: AbsencePlan) -> None:
        """Write exactly what the plan decided, and say what happened."""
        result = self._services.absence.book_plan(plan)
        self._after_write(
            result.message(f"of {plan.absence_type.phrase} booked"),
            ok=result.success,
            warning=result.warning,
        )

    def action_remove(self) -> None:
        """Clear the selection, asking first when there is a lot of it.

        The question names what would go, not how much: nine days of annual
        leave and nine sick mornings are different things to agree to.
        """
        selection = self.selection
        plan = self._services.absence.removal_plan(selection.start, selection.end)
        if plan.is_empty:
            self.status("Nothing booked to remove", Tone.WARN)
            return

        if plan.count <= REMOVE_THRESHOLD:
            self._clear(plan)
            return

        def confirm(answer: bool | None) -> None:  # noqa: FBT001 - Textual passes a dismissal result positionally
            if answer:
                self._clear(plan)

        self.app.push_screen(
            ConfirmModal(
                f"Removing from {selection.label()}\n\n{plan.summary}",
                title="Remove leave?",
            ),
            callback=confirm,
        )

    def _clear(self, plan: RemovalPlan) -> None:
        result = self._services.absence.remove_plan(plan)
        self._after_write(result.message("removed"), ok=result.success)

    def action_edit(self, kind: AbsenceType = AbsenceType.ANNUAL) -> None:
        """The modal, for the cases a single keystroke cannot express.

        Opened on the type that was pressed and the portion that was cycled, so
        `O` asks for a note about other absence, not about annual leave.
        """
        selection = self.selection

        def book(booking: AbsenceBooking | None) -> None:
            if booking is None:
                return
            self._offer(
                self._services.absence.plan(
                    booking.when,
                    booking.until,
                    booking.kind,
                    booking.portion,
                    note=booking.note,
                    available_toil_days=available_toil_days(self._services),
                )
            )

        self.app.push_screen(
            AbsenceModal(
                selection.start,
                kind,
                until=None if selection.single else selection.end,
                portion=self.portion,
                remaining=self._services.absence.get_remaining_annual_leave(
                    selection.start
                ),
                toil_days=available_toil_days(self._services),
            ),
            callback=book,
        )

    def _after_write(
        self, message: str, *, ok: bool, warning: str | None = None
    ) -> None:
        invalidate_services(self._services)
        self.rebuild()
        self.status(
            warning or message, Tone.WARN if warning else (Tone.OK if ok else Tone.ERR)
        )

    # --- moving -----------------------------------------------------------

    def action_today(self) -> None:
        self.calendar.go_to(wallclock.today())

    def action_go_to_date(self) -> None:
        def apply(when: date | None) -> None:
            if when is None:
                return
            if not self.period.contains(when):
                self.period = self.period.go_to(when)
                self.rebuild()
            self.calendar.go_to(when)

        self.app.push_screen(GoToDateModal(self.selection.head), callback=apply)

    def action_back(self) -> None:
        self.dismiss(None)

    # --- reporting --------------------------------------------------------

    def status(self, message: str, tone: Tone = Tone.NEUTRAL) -> None:
        for footer in self.query(AppFooter):
            footer.set_status(message, tone)

    def refresh_modules(self, scope: Scope) -> None:
        """Redraw on an external change, so the app can treat every screen alike."""
        if scope & Scope.SETTINGS:
            self.period = self.period.with_year_start(
                self._services.settings.get_leave_year_start()
            )
        self.rebuild()


def preview(plan: AbsencePlan) -> str:
    """The plan as a few lines to read before agreeing to it.

    A summary, not the day-by-day listing the command line prints. Every figure
    comes off the plan, so this cannot disagree with what is about to be
    written.
    """
    lines = [plan.headline]
    lines.extend(f"  — {reason}" for reason in plan.reasons)
    # Weekends and bank holidays are both passed over, and counted apart: a
    # bank holiday is a day off leave would otherwise have been spent on.
    passed_over = Counter(day.verdict for day in plan.skipped)
    weekends = passed_over[Verdict.NON_WORKING]
    holidays = passed_over[Verdict.BANK_HOLIDAY]
    if weekends:
        lines.append(f"  — {weekends} non-working {plural(weekends, 'day')}")
    if holidays:
        lines.append(f"  — {holidays} {plural(holidays, 'bank holiday')}")

    balances = tuple(
        balance
        for balance in plan.annual_balances
        if balance.before is not None and balance.after is not None
    )
    if plan.absence_type.draws_down_entitlement and balances:
        lines.append("")
        for balance in balances:
            label = (
                "Annual leave" if len(balances) == 1 else f"Annual leave {balance.year}"
            )
            lines.append(
                f"{label}: {fmt_days(balance.before or 0)}"
                f" → {fmt_days(balance.after or 0)} left"
            )
    if plan.warning:
        lines.append(plan.warning)
    return "\n".join(lines)


def working_day(
    when: date, ledgers: Mapping[date, DayLedger], pattern: AbstractSet[int]
) -> bool:
    """Whether leave spent on a date would cost a day.

    The drawn year knows about bank holidays; the weekday pattern answers for a
    date outside it, which a selection reaches by being dragged past the end of
    the drawn year.
    """
    day = ledgers.get(when)
    if day is None:
        return when.weekday() in pattern
    return day.is_working_day and not day.is_holiday


def nothing_doing(plan: AbsencePlan) -> str:
    """Why an empty plan is empty, in one line."""
    return plan.reasons[0] if plan.reasons else "Nothing to book in that selection"
