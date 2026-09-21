"""One dashboard module at a time, on a screen that carries only its context.

A module reads two things off the screen it is on, the period and the moment it
is drawing, so a screen holding those two is enough to put one in any state
worth checking. The messages a module posts are collected, not acted on: a
module never does the work itself, and what is worth asserting is that it asked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.content import Content
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
from flexi.components.modules.monthview import MonthView, cell_classes, month_grid
from flexi.components.modules.records import (
    MAX_JUMP_ROWS,
    BookHere,
    DeleteHere,
    RecordsModule,
)
from flexi.components.punch import PunchStrip
from flexi.constants import AbsenceType, DayKind, Granularity, Portion
from flexi.domain.dates import DAYS_IN_WEEK, SUPPORTED_FIRST, SUPPORTED_LAST
from flexi.domain.format import MINUS, digits
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment
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
WEDNESDAY = date(2026, 6, 10)
THURSDAY = date(2026, 6, 11)
FRIDAY = date(2026, 6, 12)
SATURDAY = date(2026, 6, 13)
NOW = datetime(2026, 6, 11, 14, 32)
"""The same Thursday afternoon the rest of the suite is drawn at."""

WIDE = (90, 30)
SHORT = (90, 8)
"""Too few rows for a month of records."""


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
    first_weekday: int = 0,
) -> AsyncIterator[tuple[Pilot[None], Panel]]:
    """One module, mounted and drawn once."""
    panel = Panel(
        module,
        period=Period.containing(anchor, granularity, first_weekday=first_weekday),
        now=now,
    )
    async with Harness(panel, services).run_test(size=size) as pilot:
        await pilot.pause()
        # Settled, not pumped: a module that measures itself after its first
        # layout rebuilds its table when that measurement lands.
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


def as_delta(text: Text) -> timedelta:
    """A ± cell read back as the duration it prints."""
    printed = str(text)
    if not printed:
        return timedelta()
    sign = -1 if printed.startswith(MINUS) else 1
    hours, minutes = (int(part) for part in printed.lstrip(f"+{MINUS}").split(":"))
    return sign * timedelta(hours=hours, minutes=minutes)


@pytest.fixture
def flexi(configure: Configured) -> Services:  # noqa: F811 - the imported fixture
    """An ordinary week, with Monday worked to the minute."""
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


# ---------- the clock ----------


def test_bank_holiday_says_which_one_it_is() -> None:
    """The line under the strip is the only place the reason for a day off appears."""
    assert ClockModule()._detail(holiday("Summer bank holiday")) == (
        "Summer bank holiday"
    )


def test_untitled_bank_holiday_still_reads_as_one() -> None:
    """The calendar is fetched from GOV.UK, and a title is their field to fill."""
    assert ClockModule()._detail(holiday("")) == "Bank holiday"


async def test_bank_holiday_title_is_drawn_not_parsed(
    configure: Configured,  # noqa: F811 - the imported fixture
) -> None:
    """The title is GOV.UK's field, and square brackets are theirs to use.

    Read as markup, `[/]` closes a tag that was never opened and `[@click=...]`
    turns a holiday into a link that runs an action.
    """
    title = "Spring bank holiday [/] [@click=app.quit]"
    services = configure(holidays=((THURSDAY, title),))

    module = ClockModule()
    async with showing(module, services) as (_pilot, _panel):
        visual = module.query_one("#clock-detail", Static).render()

        assert isinstance(visual, Content)
        assert visual.plain == title
        assert not visual.spans, "nothing in a title is an action"


def test_day_taken_off_reads_as_what_was_booked() -> None:
    """Annual leave and a Sunday both expect nothing and are not the same day."""
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
    """The switch, the button and the key all arrive at one place."""
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


# ---------- what every module has in common ----------


async def test_module_reads_period_and_now_from_the_screen(
    flexi: Services,
) -> None:
    """A module that read the clock itself would draw a different second."""
    module = ClockModule()
    async with showing(module, flexi) as (_pilot, panel):
        assert module.period is panel.period
        assert module.period.anchor == THURSDAY
        assert module.now == NOW


async def test_redraw_before_the_table_has_columns_draws_nothing(
    flexi: Services,
) -> None:
    """`compose` yields the table; `on_mount` gives it its columns.

    A redraw asked for from outside lands between the two when the launch
    worker's calendar arrives, and adding rows to a table with no columns
    raises `ValueError`.
    """
    module = RecordsModule()
    async with showing(module, flexi) as (pilot, _panel):
        table = module.query_one("#records-table", ExpandableTable)
        table.clear(columns=True)

        module.rebuild()
        await pilot.pause()

        assert not table.columns, "nothing to draw into, so nothing drawn"


# ---------- the balance ----------


async def test_balance_level_with_the_contract_is_flat(
    flexi: Services,
) -> None:
    """+0:00 reads as a small surplus, so the headline loses sign and colour."""
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


async def test_headline_is_the_figure_the_command_line_prints(
    configure: Configured,  # noqa: F811 - the imported fixture
) -> None:
    """`flexi balance show` floors each term, and the headline uses the same figures."""
    services = configure(entitlement=(2026, 25.0))
    work(services, THURSDAY, hours=2 + 9 / 3600)
    expected = digits(services.ledger.balance(THURSDAY).as_shown().delta)

    module = BalanceModule()
    async with showing(module, services, anchor=THURSDAY, now=NOW):
        assert module.query_one("#balance-digits", Digits).value == expected


@pytest.mark.parametrize(
    ("minutes", "caption"),
    [
        (15, "0:15 banked"),
        (-15, "0:15 owed"),
        (23, "0:23 banked · +0.1 days"),
    ],
    ids=["a surplus under a tenth of a day", "a deficit under one", "the boundary"],
)
def test_small_balance_says_only_the_hours(minutes: int, caption: str) -> None:
    """A caption of "0 days" under a figure that is not zero contradicts it."""
    assert BalanceModule()._detail(timedelta(minutes=minutes), CONTRACTED) == caption


def test_balance_with_no_contract_stays_in_hours() -> None:
    """Contracted hours of nought is a first run, and dividing by it raises."""
    assert BalanceModule()._detail(timedelta(hours=3), timedelta()) == "+3:00"


def test_level_balance_is_muted() -> None:
    """Green is earned by a surplus; nil is not a very small one."""
    assert lean_class(timedelta()) == "muted"


# ---------- the wallet ----------


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

    Amber says the year is going faster than the allowance; red is kept for the
    one state that stops a booking outright.
    """
    allowance = Allowance(
        type=AbsenceType.ANNUAL, used=used, occurrences=1, total=total, pace=pace
    )
    assert pace_tone(allowance) is expected


# ---------- the calendar ----------


async def test_paging_the_calendar_leaves_the_period_where_it_is(
    flexi: Services,
) -> None:
    """Paging moves the grid and posts nothing, so nothing beside it is re-read."""
    module = MonthView()
    async with showing(module, flexi) as (pilot, panel):
        label = module.query_one("#calendar-label", Label)
        assert str(label.render()) == "June 2026"

        module.action_month(1)
        await pilot.pause()

        assert str(label.render()) == "July 2026"
        assert module.period.anchor == THURSDAY
        assert panel.posted == [], "browsing asked the dashboard for nothing"


async def test_arrows_beside_the_month_page_it_either_way(
    flexi: Services,
) -> None:
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


def test_month_grid_starts_the_week_where_the_period_does() -> None:
    """`Period` honours `first_day_of_week`, and the grid has to agree.

    The row tint marks every row the period touches, so a disagreement lights
    two rows for one week.
    """
    monday_first = month_grid(date(2026, 6, 1), first_weekday=0)
    sunday_first = month_grid(date(2026, 6, 1), first_weekday=6)

    assert monday_first[0] == date(2026, 6, 1), "June 2026 opens on a Monday"
    assert sunday_first[0] == date(2026, 5, 31)
    assert {when.weekday() for when in sunday_first[::7]} == {6}


async def test_arrow_key_asks_for_the_neighbouring_day(flexi: Services) -> None:
    """The grid holds no selection of its own: it asks, and redraws when told."""
    module = MonthView()
    async with showing(module, flexi) as (pilot, panel):
        module.focus()
        await pilot.press("right")
        await pilot.pause()

        assert only(panel, DateSelected).date == THURSDAY + timedelta(days=1)


@pytest.mark.parametrize(
    ("edge", "direction"), [(SUPPORTED_FIRST, -1), (SUPPORTED_LAST, 1)]
)
async def test_month_navigation_stays_in_the_supported_date_range(
    flexi: Services, edge: date, direction: int
) -> None:
    module = MonthView()
    async with showing(module, flexi, anchor=edge) as (pilot, panel):
        visible = module._visible

        module.action_move(direction)
        module.action_month(direction)
        await pilot.pause()

        assert panel.posted == []
        assert module._visible == visible


async def test_clicking_the_grid_margin_cannot_select_an_unsupported_year(
    flexi: Services,
) -> None:
    module = MonthView()
    async with showing(module, flexi, anchor=SUPPORTED_FIRST) as (pilot, panel):
        assert month_grid(SUPPORTED_FIRST, first_weekday=0)[0] < SUPPORTED_FIRST

        await pilot.click("#calendar-cell-0-0")
        await pilot.pause()

        assert panel.posted == []


async def test_headings_start_where_the_period_starts(
    flexi: Services,
) -> None:
    """The heading row and the grid read the same first weekday."""
    module = MonthView()
    async with showing(module, flexi, first_weekday=6) as (_pilot, _panel):
        row = module.query_one(".calendar-dotw-row")
        initials = [str(label.render()) for label in row.query(Label)]

        assert initials == ["S", "M", "T", "W", "T", "F", "S"]
        assert month_grid(date(2026, 6, 1), first_weekday=6)[0].weekday() == 6


async def test_clicking_a_day_asks_for_it(flexi: Services) -> None:
    """The date comes from the grid on screen, which paging moves off the anchor."""
    module = MonthView()
    async with showing(module, flexi) as (pilot, panel):
        await pilot.click("#calendar-cell-1-0")
        await pilot.pause()

        assert only(panel, DateSelected).date == MONDAY

        module.action_month(1)
        await pilot.pause()
        await pilot.click("#calendar-cell-1-0")
        await pilot.pause()

        asked = [
            message.date
            for message in panel.posted
            if isinstance(message, DateSelected)
        ]
        assert asked == [MONDAY, date(2026, 7, 6)]


async def test_clicking_furniture_asks_for_no_day(
    flexi: Services,
) -> None:
    """A click bubbles, so the month name and the paging arrow arrive here too."""
    module = MonthView()
    async with showing(module, flexi) as (pilot, panel):
        await pilot.click("#calendar-label")
        await pilot.click("#calendar-next")
        await pilot.pause()

        assert str(module.query_one("#calendar-label", Label).render()) == "July 2026"
        assert panel.posted == []


# ---------- the records ----------


async def test_record_notes_cannot_emit_terminal_controls(flexi: Services) -> None:
    note = "review\x1b]0;forged\x07\nFAKE ROW\u202e"
    ledger = DayLedger(
        date=MONDAY,
        kind=DayKind.PARTIAL,
        is_working_day=True,
        contracted=timedelta(hours=8),
        worked=timedelta(hours=1),
        expected=timedelta(hours=4),
        absences=(AbsenceSlice(1, AbsenceType.OTHER, Portion.PM, note),),
        segments=(
            Segment(
                1,
                datetime(2026, 6, 8, 9, tzinfo=UTC),
                datetime(2026, 6, 8, 10, tzinfo=UTC),
                note=note,
            ),
        ),
    )
    module = RecordsModule()
    async with showing(module, flexi):
        rows = module._children(ledger)

        for row in rows[:2]:
            displayed = cell(row.cells[1]).plain
            assert "review" in displayed
            assert not any(
                character in displayed for character in ("\x1b", "\x07", "\n", "\u202e")
            )
        assert ledger.absences[0].note == note
        assert ledger.segments[0].note == note


async def test_day_that_met_its_hours_is_drawn_muted(
    flexi: Services,
) -> None:
    """Nil is not a small surplus, and a column of ±0:00 in green would say it was."""
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


async def test_day_column_adds_up_to_the_period_under_it(
    flexi: Services,
) -> None:
    """A TOIL day withdraws from the balance without working an hour against it.

    The column carries the effect on the balance, not hours against expected.
    """
    corrected = flexi.adjustments.record(WEDNESDAY, timedelta(hours=3), "Correction")
    assert corrected.success, corrected.message
    booked = flexi.absence.book(FRIDAY, AbsenceType.FLEXI)
    assert booked.success, booked.message
    invalidate_services(flexi)

    module = RecordsModule()
    async with showing(module, flexi) as (_pilot, _panel):
        rows = module.table.visible_rows()
        days = [row for row in rows if row.key.startswith(RowKind.DAY)]
        total = next(row for row in rows if row.key == row_key(RowKind.TOTAL, "period"))
        withdrawn = next(row for row in days if row.key.endswith(str(FRIDAY)))

        corrected_row = next(row for row in days if row.key.endswith(str(WEDNESDAY)))

        assert str(cell(withdrawn.cells[3])) == "−7:24", "a day of TOIL, spent"
        assert str(cell(corrected_row.cells[3])) == "−4:24", "a day, less a correction"
        assert sum(
            (as_delta(cell(row.cells[3])) for row in days), timedelta()
        ) == as_delta(cell(total.cells[3]))


async def test_sign_column_reads_the_cells_beside_it(
    configure: Configured,  # noqa: F811 - the imported fixture
) -> None:
    """Worked and expected print in whole minutes, so ± is their difference."""
    services = configure(entitlement=(2026, 25.0))
    work(services, THURSDAY, hours=2 + 9 / 3600)

    module = RecordsModule()
    async with showing(
        module, services, granularity=Granularity.DAY, anchor=THURSDAY
    ) as (_pilot, _panel):
        rows = module.table.visible_rows()
        day = next(item for item in rows if item.key == row_key(RowKind.DAY, THURSDAY))
        total = next(
            item for item in rows if item.key == row_key(RowKind.TOTAL, "period")
        )

        assert str(cell(day.cells[2])) == "2:00"
        assert str(cell(day.cells[3])) == "−5:24"
        assert as_delta(cell(total.cells[3])) == as_delta(cell(day.cells[3]))


async def test_hidden_records_panel_offers_no_badges(
    flexi: Services,
) -> None:
    """Jump mode reads live geometry, and a panel that is not drawn has none."""
    module = RecordsModule()
    async with showing(module, flexi) as (pilot, _panel):
        module.display = False
        await pilot.pause()

        assert module.jump_row_targets() == {}


async def test_badges_stop_at_the_last_visible_row(
    flexi: Services,
) -> None:
    """A badge on a row below the viewport would send the cursor out of sight."""
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


async def test_scrolling_moves_the_badges_down_the_table(
    flexi: Services,
) -> None:
    """`visible_rows` means "not collapsed away", not "inside the viewport"."""
    module = RecordsModule()
    async with showing(module, flexi, granularity=Granularity.MONTH, size=SHORT) as (
        pilot,
        _panel,
    ):
        module.table.focus()
        for _ in range(25):
            await pilot.press("down")
        await pilot.pause()
        assert module.table.scroll_offset.y > 0, "the table really did scroll"

        targets = module.jump_row_targets()

        assert targets, "a scrolled table still has rows on screen to offer"
        region = module.table.region
        assert all(region.contains_point(offset) for offset in targets)
        assert {info.key for info in targets.values()} == {
            str(number) for number in range(1, len(targets) + 1)
        }


async def test_tall_table_numbers_only_the_first_nine(
    flexi: Services,
) -> None:
    """The tenth visible day gets no badge.

    The `break` is reachable only with ten day rows on screen at once, which no
    other test arranges.
    """
    module = RecordsModule()
    async with showing(module, flexi, granularity=Granularity.MONTH, size=(90, 30)) as (
        _pilot,
        _panel,
    ):
        targets = module.jump_row_targets()

        assert len(targets) == MAX_JUMP_ROWS
        assert {info.key for info in targets.values()} == {
            str(number) for number in range(1, MAX_JUMP_ROWS + 1)
        }


async def test_cursor_names_its_day_from_any_row(
    flexi: Services,
) -> None:
    """A session row belongs to the day above it, and booking from it means that day."""
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
    """The module names the row; the screen owns the modal and the deletion."""
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


async def test_empty_table_answers_with_no_day(
    flexi: Services,
) -> None:
    """``None`` lets the screen fall back to the day the dashboard is anchored on."""
    module = RecordsModule()
    async with showing(module, flexi) as (pilot, panel):
        module.table.set_groups(())
        await pilot.pause()
        assert module.selected_date() is None

        module.action_book_here()
        await pilot.pause()

        assert only(panel, BookHere).iso is None


# ---------- the punch strip ----------


def test_strip_with_no_day_to_draw_is_blank() -> None:
    """The clock module composes its strip before it has read the database."""
    assert str(PunchStrip(now=NOW).render()) == ""


def test_new_day_does_not_change_the_strip_window() -> None:
    """The window is the axis the rows share, and it is not the day's to change.

    Omitting it leaves the axis alone; a default would rescale every strip.
    """
    window = Window.parse("06:00", "22:00")
    strip = PunchStrip(window=window, now=NOW)

    strip.set_ledger(holiday("Summer bank holiday"), now=NOW)

    assert strip.window is window


def test_day_with_no_ledger_is_drawn_plain() -> None:
    """The grid squares off a month, so `ledgers.get` is `None` at its corners.

    The cell still says which month it is in and whether it is today.
    """
    period = Period.containing(THURSDAY, Granularity.MONTH)
    last_month = date(2026, 5, 31)

    classes = cell_classes(
        last_month, None, period, today=THURSDAY, showing=THURSDAY.replace(day=1)
    )

    assert "not-current-month" in classes
    assert not any(c.startswith(("day-", "absence-")) for c in classes), (
        "a cell with no ledger claims no kind of day"
    )


# ---------- the calendar's period window ----------


def window_of(period: Period, showing: date, *, first_weekday: int = 0) -> list[date]:
    """The grid days `cell_classes` would tint for a period."""
    return [
        day
        for day in month_grid(showing, first_weekday=first_weekday)
        if "in-period" in cell_classes(day, None, period, today=day, showing=showing)
    ]


@pytest.mark.parametrize(
    ("granularity", "expected"),
    [
        (Granularity.DAY, 1),
        (Granularity.WEEK, DAYS_IN_WEEK),
        (Granularity.MONTH, 30),
    ],
)
def test_window_covers_exactly_the_period_it_is_showing(
    granularity: Granularity, expected: int
) -> None:
    """Counted off the grid that is looked at, not off the period."""
    period = Period.containing(THURSDAY, granularity)

    assert len(window_of(period, THURSDAY.replace(day=1))) == expected


def test_week_window_lands_on_one_grid_row() -> None:
    """A Sunday-first week on a Monday-first grid would tint fourteen days."""
    for first_weekday in (0, 6):
        period = Period.containing(
            THURSDAY, Granularity.WEEK, first_weekday=first_weekday
        )
        tinted = window_of(period, THURSDAY.replace(day=1), first_weekday=first_weekday)
        grid = month_grid(THURSDAY.replace(day=1), first_weekday=first_weekday)

        assert len(tinted) == DAYS_IN_WEEK
        rows = {grid.index(day) // DAYS_IN_WEEK for day in tinted}
        assert len(rows) == 1, f"a week spilled across {len(rows)} rows"


def test_year_window_starts_where_the_leave_year_does() -> None:
    """The days before a leave year opens belong to the year just ended."""
    opens = (8, 13)
    period = Period.containing(date(2026, 8, 27), Granularity.YEAR, year_start=opens)

    tinted = window_of(period, date(2026, 8, 1))

    assert min(tinted) == date(2026, 8, 13), "it opens on the 13th"
    assert date(2026, 8, 12) not in tinted, "and the 12th is last year's"


def test_paging_the_grid_away_from_the_period_tints_nothing() -> None:
    """The grid and the period are separate, so a month clear of it is blank."""
    period = Period.containing(THURSDAY, Granularity.WEEK)

    assert window_of(period, date(2026, 9, 1)) == []


def test_selected_day_is_marked_and_still_tinted() -> None:
    """Two devices on one cell: the window tints, the selection reverses."""
    period = Period.containing(THURSDAY, Granularity.WEEK)

    classes = cell_classes(
        THURSDAY, None, period, today=THURSDAY, showing=THURSDAY.replace(day=1)
    )

    assert {"selected", "in-period", "today"} <= set(classes)
