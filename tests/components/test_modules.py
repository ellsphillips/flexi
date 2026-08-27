"""One dashboard module at a time, on a screen that is nothing but its context.

The dashboard mounts five modules against six weeks of seeded work, which makes
it the wrong instrument for asking what a single panel does with a day that met
its hours exactly, a month too long to fit, or a switch flipped by hand. A
module reads only two things off the screen it is on — the period and the moment
it is drawing — so a screen that carries those two and nothing else is enough to
put one in any state worth checking, and costs a fraction of the real thing.

The messages a module posts are collected rather than acted on: a module never
does the work itself, and what is worth asserting is that it asked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.message import Message
from textual.pilot import Pilot
from textual.screen import Screen
from textual.widgets import Button, Digits, Label, Static, Switch

from flexi.components.allowance import pace_tone
from flexi.components.common import Tone
from flexi.components.expandable import ExpandableTable, RowKind, row_key
from flexi.components.modules.balance import BalanceModule, lean_class
from flexi.components.modules.base import Module
from flexi.components.modules.clock import ClockModule
from flexi.components.modules.monthview import MonthView, month_grid
from flexi.components.modules.records import (
    MAX_JUMP_ROWS,
    BookHere,
    DeleteHere,
    RecordsModule,
)
from flexi.components.punch import PunchStrip
from flexi.constants import AbsenceType, DayKind, Granularity, Portion
from flexi.domain.ledger import AbsenceSlice, DayLedger
from flexi.domain.period import Period
from flexi.domain.punch import Window
from flexi.domain.wallet import Allowance
from flexi.messages import DateSelected
from flexi.services.registry import Services, invalidate_services, zero_balance
from tests.conftest import settled
from tests.services.conftest import (  # noqa: F401 - `configure` is used as a fixture
    CONTRACTED,
    Configured,
    configure,
    work,
)

MONDAY = date(2026, 6, 8)
THURSDAY = date(2026, 6, 11)
SATURDAY = date(2026, 6, 13)
NOW = datetime(2026, 6, 11, 14, 32)
"""The same Thursday afternoon the rest of the suite is drawn at."""

WIDE = (90, 30)
SHORT = (90, 8)
"""Too few rows for a month of records, which is the point of it."""


class Panel(Screen[None]):
    """A screen that is the two facts a module reads, and a message inbox."""

    def __init__(self, module: Module, *, period: Period, now: datetime) -> None:
        super().__init__()
        self.module = module
        self.period = period
        self.now = now
        self.posted: list[Message] = []

    def compose(self) -> ComposeResult:
        yield self.module

    def on_date_selected(self, message: DateSelected) -> None:
        self.posted.append(message)

    def on_book_here(self, message: BookHere) -> None:
        self.posted.append(message)

    def on_delete_here(self, message: DeleteHere) -> None:
        self.posted.append(message)

    def on_clock_module_toggle(self, message: ClockModule.Toggle) -> None:
        self.posted.append(message)


class Harness(App[None]):
    """Everything a module reaches for: a service registry, and one screen."""

    def __init__(self, panel: Panel, services: Services) -> None:
        super().__init__()
        self.services = services
        self.panel = panel

    def get_default_screen(self) -> Screen[None]:
        return self.panel


@asynccontextmanager
async def showing(
    module: Module,
    services: Services,
    *,
    anchor: date = THURSDAY,
    granularity: Granularity = Granularity.WEEK,
    now: datetime = NOW,
    size: tuple[int, int] = WIDE,
) -> AsyncIterator[tuple[Pilot[None], Panel]]:
    """One module, mounted and drawn once."""
    panel = Panel(module, period=Period.containing(anchor, granularity), now=now)
    async with Harness(panel, services).run_test(size=size) as pilot:
        await pilot.pause()
        # Settled, not merely pumped: a module that measures itself after its
        # first layout rebuilds its table when that measurement lands, and a
        # body that starts beforehand has its set-up overwritten mid-test.
        await settled(pilot)
        yield pilot, panel


def only[M: Message](panel: Panel, kind: type[M]) -> M:
    """The one message of a kind the module posted, asserted to be alone."""
    found = [message for message in panel.posted if isinstance(message, kind)]
    assert len(found) == 1, f"expected one {kind.__name__}, got {len(found)}"
    return found[0]


def cell(text: object) -> Text:
    assert isinstance(text, Text)
    return text


@pytest.fixture
def flexi(configure: Configured) -> Services:  # noqa: F811 - the imported fixture
    """A set-up Flexi with an ordinary week behind it.

    Monday is worked to the minute, which is the case the seeded demo never
    produces and the one the ± column is most easily wrong about.
    """
    services = configure(entitlement=(2026, 25.0))
    work(services, MONDAY, 7.4)
    work(services, MONDAY + timedelta(days=1), 9.0)
    return services


def holiday(title: str) -> DayLedger:
    return DayLedger(
        date=date(2026, 8, 31),
        kind=DayKind.HOLIDAY,
        is_working_day=True,
        contracted=CONTRACTED,
        worked=timedelta(),
        expected=timedelta(),
        holiday_title=title,
    )


# -- the clock ---------------------------------------------------------------


def test_a_bank_holiday_says_which_one_it_is() -> None:
    """Christmas Day answered with "Not arrived" is a nag about a day off.

    The line under the strip is the only place the reason a day expects nothing
    appears, so it names the holiday rather than reporting the absence of work.
    """
    assert ClockModule()._detail(holiday("Summer bank holiday")) == (
        "Summer bank holiday"
    )


def test_a_bank_holiday_with_no_name_cached_still_reads_as_one() -> None:
    """The calendar is fetched from GOV.UK, and a title is their field to fill."""
    assert ClockModule()._detail(holiday("")) == "Bank holiday"


def test_a_day_taken_off_reads_as_what_was_booked() -> None:
    """Annual leave and a Sunday both expect nothing and are not the same day.

    Only a day with no work on it says this: clocking in on a booked afternoon
    is allowed, and the line then has to go back to reporting hours.
    """
    booked = DayLedger(
        date=THURSDAY,
        kind=DayKind.ABSENT,
        is_working_day=True,
        contracted=CONTRACTED,
        worked=timedelta(),
        expected=timedelta(),
        absences=(AbsenceSlice(1, AbsenceType.ANNUAL, Portion.FULL),),
    )
    assert ClockModule()._detail(booked) == "Annual leave"


async def test_flipping_the_switch_asks_the_screen_to_clock_in(
    flexi: Services,
) -> None:
    """The switch, the button and the key all have to arrive at one place.

    And a redraw writing the switch back to what the database says must not read
    as a second request — that is how the module clocked out again the instant
    it had clocked in.
    """
    module = ClockModule()
    async with showing(module, flexi) as (pilot, panel):
        switch = module.query_one("#clock-switch", Switch)
        assert switch.value is False, "nobody is on the clock this Thursday"

        module.rebuild()
        await pilot.pause()
        assert panel.posted == [], "a redraw is not a request to clock in"

        switch.value = True
        await pilot.pause()

        assert only(panel, ClockModule.Toggle)


# -- what every module has in common -----------------------------------------


async def test_a_module_takes_its_period_and_its_moment_from_the_screen(
    flexi: Services,
) -> None:
    """Five panels drawing the same week is the whole point of putting it there.

    A module that read the clock itself would draw a different second from its
    neighbour, and no test could pin either.
    """
    module = ClockModule()
    async with showing(module, flexi) as (_pilot, panel):
        assert module.period is panel.period
        assert module.period.anchor == THURSDAY
        assert module.now == NOW


async def test_a_redraw_before_the_table_has_columns_draws_nothing(
    flexi: Services,
) -> None:
    """`compose` yields the table; `on_mount` gives it its columns.

    A redraw asked for from outside can land between the two — the launch
    worker's bank-holiday calendar arriving is the real case — and adding rows
    to a table with no columns raises `ValueError: More values provided than
    there are columns`. On a worker thread Textual reports that as the whole
    application failing.
    """
    module = RecordsModule()
    async with showing(module, flexi) as (pilot, _panel):
        table = module.query_one("#records-table", ExpandableTable)
        table.clear(columns=True)

        module.rebuild()
        await pilot.pause()

        assert not table.columns, "nothing to draw into, so nothing drawn"


# -- the balance -------------------------------------------------------------


async def test_a_balance_level_with_the_contract_is_drawn_flat(
    flexi: Services,
) -> None:
    """+0:00 reads as a small surplus, and the point of nil is that there is not one.

    So the headline loses its sign and its colour, and the caption says in words
    what a reader would otherwise have to infer from a figure that looks like
    every other figure.
    """
    zero_balance(flexi, as_of=SATURDAY)
    invalidate_services(flexi)

    module = BalanceModule()
    sunday = datetime(2026, 6, 14, 10, 0)
    async with showing(module, flexi, anchor=sunday.date(), now=sunday):
        readout = module.query_one("#balance-digits", Digits)
        assert readout.value == "0:00", "no sign, because there is no surplus"
        assert readout.has_class("muted")
        assert str(module.query_one("#balance-detail", Static).render()) == (
            "Level with contracted hours"
        )


def test_a_balance_with_no_contract_behind_it_is_left_in_hours() -> None:
    """Days are what a balance is spent in, and it takes a contract to say so.

    Contracted hours of nought is a first run mid-setup, and dividing by it is
    the exception this branch exists to avoid.
    """
    assert BalanceModule()._detail(timedelta(hours=3), timedelta()) == "+3:00"


def test_a_level_balance_is_muted_rather_than_coloured() -> None:
    """Green is earned by a surplus; nil is not a very small one."""
    assert lean_class(timedelta()) == "muted"


# -- the wallet --------------------------------------------------------------


@pytest.mark.parametrize(
    ("used", "total", "pace", "expected"),
    [
        (25.0, 25.0, 20.0, Tone.ERR),
        (10.0, 25.0, 5.0, Tone.WARN),
        (5.0, 25.0, 5.0, Tone.OK),
        (2.0, None, None, Tone.NEUTRAL),
    ],
    ids=["spent", "ahead of an even spread", "on track", "no entitlement"],
)
def test_only_an_allowance_with_nothing_left_is_red(
    used: float, total: float | None, pace: float | None, expected: Tone
) -> None:
    """Spending leave early is a plan, not a fault.

    Amber says the year is going faster than the allowance; red is reserved for
    the one state that stops you booking at all, or the wallet cries wolf every
    April and is ignored by the August it matters in.
    """
    allowance = Allowance(
        type=AbsenceType.ANNUAL, used=used, occurrences=1, total=total, pace=pace
    )
    assert pace_tone(allowance) is expected


# -- the calendar ------------------------------------------------------------


async def test_paging_the_calendar_leaves_the_period_where_it_is(
    flexi: Services,
) -> None:
    """Looking ahead to see where the bank holidays fall is not a decision.

    The records table beside it must not be re-read because somebody browsed,
    so paging moves the grid and tells nobody.
    """
    module = MonthView()
    async with showing(module, flexi) as (pilot, panel):
        label = module.query_one("#calendar-label", Label)
        assert str(label.render()) == "June 2026"

        module.action_month(1)
        await pilot.pause()

        assert str(label.render()) == "July 2026"
        assert module.period.anchor == THURSDAY
        assert panel.posted == [], "browsing asked the dashboard for nothing"


async def test_the_arrows_beside_the_month_page_it_either_way(
    flexi: Services,
) -> None:
    """A visible control teaches the keys beside it, and makes a pointer work."""
    module = MonthView()
    async with showing(module, flexi) as (pilot, _panel):
        label = module.query_one("#calendar-label", Label)

        module.query_one("#calendar-next", Button).press()
        await pilot.pause()
        assert str(label.render()) == "July 2026"

        module.query_one("#calendar-prev", Button).press()
        await pilot.pause()
        module.query_one("#calendar-prev", Button).press()
        await pilot.pause()

        assert str(label.render()) == "May 2026"


def test_the_month_grid_starts_on_the_day_the_week_is_configured_to_start() -> None:
    """It started on Monday whatever the period beside it was doing.

    `Period` honours `first_day_of_week`; the grid did not. Set the week to
    start on Sunday and the row tint -- which marks every row the period touches
    -- lit two rows for one week, fourteen days presented as this week, under
    headings that still read M T W T F S S.
    """
    monday_first = month_grid(date(2026, 6, 1), first_weekday=0)
    sunday_first = month_grid(date(2026, 6, 1), first_weekday=6)

    assert monday_first[0] == date(2026, 6, 1), "June 2026 opens on a Monday"
    assert sunday_first[0] == date(2026, 5, 31)
    assert {when.weekday() for when in sunday_first[::7]} == {6}


async def test_an_arrow_key_asks_for_the_neighbouring_day(flexi: Services) -> None:
    """The grid holds no selection of its own: it asks, and redraws when told.

    Two places that both believe they know which day is selected is how the
    calendar and the records table came to disagree about the week.
    """
    module = MonthView()
    async with showing(module, flexi) as (pilot, panel):
        module.focus()
        await pilot.press("right")
        await pilot.pause()

        assert only(panel, DateSelected).date == THURSDAY + timedelta(days=1)


async def test_enter_asks_for_the_day_the_grid_is_anchored_on(
    flexi: Services,
) -> None:
    """`enter` is what commits, so it has to say something even when nothing moved."""
    module = MonthView()
    async with showing(module, flexi) as (pilot, panel):
        module.action_select()
        await pilot.pause()

        assert only(panel, DateSelected).date == THURSDAY


# -- the records ---------------------------------------------------------------


async def test_a_day_that_met_its_hours_exactly_is_drawn_without_a_sign(
    flexi: Services,
) -> None:
    """Nil is not a small surplus, and a column of ±0:00 in green would say it was.

    The muted style is what separates "level" from "ahead by a minute" at a
    glance down the column, which is the only way that column is ever read.
    """
    module = RecordsModule()
    async with showing(module, flexi) as (_pilot, _panel):
        row = next(
            item
            for item in module.table.visible_rows()
            if item.key == row_key(RowKind.DAY, MONDAY)
        )
        delta = cell(row.cells[3])

        assert str(delta) == "0:00"
        assert delta.style == module.get_component_rich_style("record--muted")


async def test_a_records_panel_the_layout_has_dropped_offers_no_badges(
    flexi: Services,
) -> None:
    """Jump mode reads live geometry, and a panel that is not drawn has none.

    A badge at the offset a hidden panel used to occupy is a key that jumps into
    whatever has since slid under it — on the mode whose whole appeal is that
    pressing it costs nothing.
    """
    module = RecordsModule()
    async with showing(module, flexi) as (pilot, _panel):
        module.display = False
        await pilot.pause()

        assert module.jump_row_targets() == {}


async def test_only_nine_days_are_numbered_and_only_where_they_can_be_seen(
    flexi: Services,
) -> None:
    """There are nine number keys, and a month has thirty-one days.

    A badge on a row scrolled past the bottom of the table would send the cursor
    somewhere the eye cannot follow, so the offer stops at the last visible row.
    """
    module = RecordsModule()
    async with showing(module, flexi, granularity=Granularity.MONTH, size=SHORT) as (
        _pilot,
        _panel,
    ):
        rows = module.table.visible_rows()
        assert len(rows) > MAX_JUMP_ROWS, "a month is longer than the offer"

        targets = module.jump_row_targets()
        assert 0 < len(targets) < MAX_JUMP_ROWS, "the short table shows fewer"
        assert {info.key for info in targets.values()} == {
            str(number) for number in range(1, len(targets) + 1)
        }
        region = module.table.region
        assert all(region.contains_point(offset) for offset in targets)


async def test_the_cursor_names_its_day_from_anywhere_inside_the_group(
    flexi: Services,
) -> None:
    """A session row belongs to the day above it, and booking from it means that day.

    Otherwise opening a row to check why Monday is short and pressing `b` there
    books against nothing at all.
    """
    module = RecordsModule()
    async with showing(module, flexi) as (pilot, _panel):
        table = module.table
        table.focus_key(row_key(RowKind.DAY, MONDAY))
        await pilot.pause()
        assert module.selected_date() == MONDAY.isoformat()

        table.toggle(row_key(RowKind.DAY, MONDAY))
        await pilot.pause()
        session = next(
            row for row in table.visible_rows() if row.key.startswith(RowKind.SESSION)
        )
        table.focus_key(session.key)
        await pilot.pause()
        assert module.selected_date() == MONDAY.isoformat()

        table.focus_key(f"{RowKind.TOTAL}period")
        await pilot.pause()
        assert module.selected_date() is None, "the period line is not a day"


async def test_booking_and_deleting_carry_whatever_the_cursor_is_on(
    flexi: Services,
) -> None:
    """The module names the row; the screen owns the modal and the deletion.

    A module that booked for itself would need the flexi balance, the absence
    service and a screen to push a dialog onto — which is three reasons the
    decision belongs one level up.
    """
    module = RecordsModule()
    async with showing(module, flexi) as (pilot, panel):
        table = module.table
        tuesday = row_key(RowKind.DAY, MONDAY + timedelta(days=1))
        table.focus_key(tuesday)
        await pilot.pause()

        module.action_book_here()
        await pilot.pause()
        assert only(panel, BookHere).iso == (MONDAY + timedelta(days=1)).isoformat()

        table.toggle(tuesday)
        await pilot.pause()
        session = next(
            row for row in table.visible_rows() if row.key.startswith(RowKind.SESSION)
        )
        table.focus_key(session.key)
        await pilot.pause()

        module.action_delete_here()
        await pilot.pause()
        assert only(panel, DeleteHere).key == session.key


async def test_with_nothing_under_the_cursor_the_screen_is_told_so(
    flexi: Services,
) -> None:
    """An empty table still has to answer, and it must not answer with a guess.

    ``None`` is what lets the screen fall back to the day the dashboard is
    anchored on, rather than the module inventing a date nobody chose.
    """
    module = RecordsModule()
    async with showing(module, flexi) as (pilot, panel):
        module.table.set_groups(())
        await pilot.pause()
        assert module.selected_date() is None

        module.action_book_here()
        await pilot.pause()

        assert only(panel, BookHere).iso is None


# -- the punch strip ---------------------------------------------------------


def test_a_strip_with_no_day_to_draw_is_blank() -> None:
    """The clock module composes its strip before it has read the database."""
    assert str(PunchStrip(now=NOW).render()) == ""


def test_a_strip_told_only_a_new_day_keeps_the_window_it_draws_in() -> None:
    """The window is the axis the rows share, and it is not the day's to change.

    Passing it on every redraw is what the clock module does; not passing it has
    to leave the axis alone rather than silently reverting to the default, or
    one redraw would rescale every strip on the screen.
    """
    window = Window.parse("06:00", "22:00")
    strip = PunchStrip(window=window, now=NOW)

    strip.set_ledger(holiday("Summer bank holiday"), now=NOW)

    assert strip.window is window


async def test_a_day_the_ledger_says_nothing_about_is_drawn_plain(
    flexi: Services,
) -> None:
    """The grid squares off a month, so its corners belong to other ones.

    `ledgers.get(when)` returns `None` for those, and a cell with no ledger
    behind it must still say where it is — which month it belongs to, whether
    it is today, whether it is selected — without claiming a kind of day it
    knows nothing about.
    """
    module = MonthView()
    async with showing(module, flexi, granularity=Granularity.MONTH) as (_p, _panel):
        period = Period.containing(THURSDAY, Granularity.MONTH)
        last_month = date(2026, 5, 31)

        classes = module._cell_classes(last_month, None, period, THURSDAY)

        assert "not-current-month" in classes
        assert not any(c.startswith(("day-", "absence-")) for c in classes), (
            "a cell with no ledger claims no kind of day"
        )
