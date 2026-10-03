"""Feature 3: a row per day, opening to the day's breakdown."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import event
from textual.pilot import Pilot
from textual.widgets import Input, RadioSet

from flexi.app import FlexiApp
from flexi.components.expandable import (
    ExpandableTable,
    RowKind,
    row_key,
)
from flexi.components.modules.clock import ClockModule
from flexi.components.modules.monthview import MonthView
from flexi.components.modules.records import DeleteHere, RecordsModule
from flexi.components.modules.wallet import BookRequested, WalletModule
from flexi.components.progress import ProgressRail, TimeProgress
from flexi.config import CONFIG
from flexi.constants import AbsenceType
from flexi.messages import Scope
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.modals import AbsenceModal, ConfirmModal, CorrectionModal
from flexi.services.absence import PLAN_CHANGED
from flexi.services.registry import adjust_balance, zero_balance
from flexi.theme import colour
from tests.conftest import sessions_on, settled
from tests.tui.conftest import (
    WIDE,
    AppFactory,
    dashboard,
    screen_text,
    showing,
    status_text,
)


def table(app: FlexiApp) -> ExpandableTable:
    return app.screen.query_one("#records-table", ExpandableTable)


def absence_key(app: FlexiApp, when: date) -> str:
    """The row key of the booking on a day, looked up by id.

    The key carries a database id, so writing one out would fix the test to the
    order the demo seed inserts its absences in.
    """
    booked = app.services.absence.in_range(when, when)
    assert booked, f"the seed has nothing booked on {when}"
    return f"{RowKind.ABSENCE}{booked[0].id}"


async def prefilled(app: FlexiApp, pilot: Pilot[None]) -> tuple[date, AbsenceType]:
    """The day and the type the booking dialog arrived already holding.

    Read off the fields, because the promise of a pre-filled dialog is that what
    it shows is what it books. The modal chooses its type in work deferred past
    its first layout, so this waits on `settled` and not on a bare `pause`.
    """
    await settled(pilot)
    modal = showing(app, AbsenceModal)
    when = date.fromisoformat(modal.query_one("#absence-date", Input).value)
    pressed = modal.query_one("#absence-type", RadioSet).pressed_button
    assert pressed is not None, "the dialog opened with no type chosen"
    return when, AbsenceType(pressed.name)


async def test_week_is_seven_rows_and_a_total(app_factory: AppFactory) -> None:
    """Every day in the period appears, worked or not, then the period line."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        rows = table(app).visible_rows()
        assert len([row for row in rows if row.kind == RowKind.DAY]) == 7
        assert rows[-1].key == "t-period"


# --- the keyboard at launch -------------------------------------------------

TODAY = date(2026, 6, 11)
"""The Thursday the frozen clock is standing on."""


async def test_the_rows_have_the_keyboard_from_launch(
    app_factory: AppFactory,
) -> None:
    """The cursor starts on the day `n` records on, under the keys that move it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)

        assert app.focused is widget
        assert widget.cursor_key == row_key(RowKind.DAY, TODAY)


async def test_space_and_x_work_from_launch(app_factory: AppFactory) -> None:
    """Open yesterday and void its first session, with no click and no jump."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        await pilot.press("up", "space", "down", "x")
        await pilot.pause()

        assert "on Wed 10 Jun" in showing(app, ConfirmModal)._question


async def test_the_cursor_follows_the_period_to_its_anchor(
    app_factory: AppFactory,
) -> None:
    """Moving the period moves the cursor with it, so it and `n` agree."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        await pilot.press("right_square_bracket")
        await pilot.pause()

        anchor = dashboard(app).period.anchor
        assert anchor == TODAY + timedelta(days=7)
        assert table(app).cursor_key == row_key(RowKind.DAY, anchor)


async def test_a_redraw_leaves_the_cursor_where_it_was_put(
    app_factory: AppFactory,
) -> None:
    """Only a moved anchor moves the cursor: a write or a minute does not."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("up", "up")
        await pilot.pause()

        dashboard(app).refresh_modules(Scope.ALL)
        await pilot.pause()

        assert table(app).cursor_key == row_key(RowKind.DAY, TODAY - timedelta(days=2))


async def test_a_cancelled_dialog_leaves_the_cursor_where_it_was(
    app_factory: AppFactory,
) -> None:
    """The rows take the keyboard once; after that it is the user's to move.

    Taking it back whenever the dashboard showed again would put the cursor
    back on today after every cancelled `n`.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("up", "up", "n")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.focused is table(app)
        assert table(app).cursor_key == row_key(RowKind.DAY, TODAY - timedelta(days=2))


async def test_closing_help_leaves_the_calendar_the_keyboard(
    app_factory: AppFactory,
) -> None:
    """The screen's own focus comes back with it, not the rows' first claim."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        calendar = app.screen.query_one(MonthView)
        calendar.focus()
        await pilot.pause()

        await pilot.press("question_mark")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.focused is calendar


def cursor_ground(widget: ExpandableTable) -> str:
    """The colour behind the row under the cursor, as `#rrggbb`."""
    ground = widget.get_component_rich_style("datatable--cursor").bgcolor
    assert ground is not None
    assert ground.triplet is not None
    return ground.triplet.hex


async def test_the_cursor_is_lit_only_while_the_rows_have_the_keyboard(
    app_factory: AppFactory,
) -> None:
    """Away from the table its cursor steps back, as the theme's blurred cursor.

    Lit, it reads as the day the keys act on, and away from the table `n` acts
    on the anchor instead.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        assert cursor_ground(widget) == colour("c-accent-deep").lower()

        app.screen.query_one(ClockModule).focus()
        await pilot.pause()

        assert cursor_ground(widget) == colour("c-line-soft").lower()


async def test_tab_comes_round_to_the_rows_through_live_stops(
    app_factory: AppFactory,
) -> None:
    """No stop on the way is a container that shows nothing and does nothing.

    The left column's scroller and the records panel were both stops, and on
    either the arrows did nothing. Shift+Tab leaves the table as Tab does.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        stops = []
        for _ in range(20):
            await pilot.press("tab")
            await pilot.pause()
            if app.focused is widget:
                break
            stops.append(app.focused)

        assert app.focused is widget, f"Tab never came back: {stops}"
        dead = {
            app.screen.query_one("#dashboard-controls"),
            app.screen.query_one(RecordsModule),
        }
        assert not dead & set(stops), stops

        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.focused is stops[-1]


async def test_space_opens_the_day_under_the_cursor(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key(f"{RowKind.DAY}2026-06-08")
        await pilot.pause()

        before = len(widget.visible_rows())
        await pilot.press("space")
        await pilot.pause()

        assert len(widget.visible_rows()) > before
        assert any(row.key.startswith(RowKind.SESSION) for row in widget.visible_rows())


@pytest.mark.parametrize(
    ("keys", "saturday_in_view"),
    [(("down",), False), (("down", "down", "up"), True)],
    ids=["on-the-last-row-in-view", "one-row-above-it"],
)
async def test_space_near_the_foot_of_the_table_shows_what_it_opened(
    app_factory: AppFactory, keys: tuple[str, ...], *, saturday_in_view: bool
) -> None:
    """At 80x24 the week runs on below the table, and an opened day ran with it.

    On the last row in view, space looked to do nothing. On the row above, the
    table scrolled up one and hid what it had opened.
    """
    app = app_factory()
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot)
        await pilot.press(*keys)
        assert table(app).cursor_key == row_key(RowKind.DAY, TODAY + timedelta(days=1))
        assert ("Sat 13" in screen_text(app)) is saturday_in_view

        await pilot.press("space")
        await pilot.pause()

        drawn = screen_text(app)
        assert "Fri 12" in drawn, "the day stays in view"
        assert "└ expected" in drawn, "and so do the rows it opened"


async def test_expanding_does_not_move_the_cursor(app_factory: AppFactory) -> None:
    """The cursor is restored by key, so rows inserted above do not move it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        target = f"{RowKind.DAY}2026-06-10"
        widget.focus_key(target)
        await pilot.pause()
        assert widget.cursor_key == target

        widget.toggle(f"{RowKind.DAY}2026-06-08")  # a row above the cursor
        await pilot.pause()
        assert widget.cursor_key == target


async def test_day_with_nothing_recorded_does_not_open(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        saturday = f"{RowKind.DAY}2026-06-13"
        assert widget.toggle(saturday) is False
        assert saturday not in widget.expanded


async def test_shift_space_opens_and_closes_everything(app_factory: AppFactory) -> None:
    """Expand-all inverts the majority, so one key does the visible thing."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.expand_all(expanded=True)
        await pilot.pause()
        assert len(widget.expanded) >= 4

        widget.expand_all()
        await pilot.pause()
        assert widget.expanded == set()


async def test_period_load_cost_does_not_grow_with_length(
    app_factory: AppFactory,
) -> None:
    """A period is read in a fixed number of queries, not one per day.

    The count is compared against a shorter period, so adding a lookup to both
    needs no new literal here.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        services = app.services
        first = dashboard(app).period.start.replace(day=1)

        def count_queries(days: int) -> int:
            statements: list[str] = []

            def record(
                conn: object, cursor: object, statement: str, *_args: object
            ) -> None:
                if statement.lstrip().upper().startswith("SELECT"):
                    statements.append(statement)

            engine = app._session.get_bind()
            event.listen(engine, "before_cursor_execute", record)
            try:
                services.ledger.invalidate()
                services.ledger.days(first, first + timedelta(days=days - 1))
            finally:
                event.remove(engine, "before_cursor_execute", record)
            return len(statements)

        assert count_queries(28) == count_queries(7)


async def test_rails_report_the_day_and_the_period(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        rails = app.screen.query_one(TimeProgress)
        day = rails.query_one("#rail-day", ProgressRail)
        period = rails.query_one("#rail-period", ProgressRail)

        assert day.label == "TODAY"
        assert 0.0 < day.share < 1.0, "the seed's today is part-worked"

        assert period.label == "WEEK"
        shown = dashboard(app).period
        week = app.services.ledger.summary(shown.start, shown.end)
        assert (period.done, period.total) == (week.worked, week.expected), (
            "the second rail reads the period on screen, not the day"
        )


async def test_period_rail_follows_the_granularity(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("m")
        await pilot.pause()
        assert app.screen.query_one("#rail-period", ProgressRail).label == "MONTH"


async def test_period_rail_hides_when_there_is_no_room(
    app_factory: AppFactory,
) -> None:
    """Below 100 columns two rails leave each other no bar, so one goes."""
    app = app_factory()
    async with app.run_test(size=(84, 28)) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#rail-day", ProgressRail).display is True
        assert app.screen.query_one("#rail-period", ProgressRail).display is False


async def test_period_total_is_in_the_border_subtitle(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        subtitle = str(app.screen.query_one(RecordsModule).border_subtitle)
        assert " of " in subtitle


async def test_period_total_agrees_with_the_wallet(
    app_factory: AppFactory,
) -> None:
    """Both read `BalanceSummary.delta`, so both carry the adjustment term."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        board = showing(app, DashboardScreen)
        before = str(table(app).get_row(f"{RowKind.TOTAL}period")[3])

        app.services.adjustments.record(
            board.period.anchor, timedelta(hours=3), "carried over"
        )
        board.refresh_modules(Scope.ALL)
        await pilot.pause()

        after = str(table(app).get_row(f"{RowKind.TOTAL}period")[3])
        assert after != before, f"the correction never reached the total ({before})"
        wallet = app.screen.query_one(WalletModule)
        assert str(wallet.border_subtitle) == f"{after.strip()} this period"


# --- booking from a row ---------------------------------------------------


async def test_a_books_an_absence_on_the_cursor_day(
    app_factory: AppFactory,
) -> None:
    """The table is the only widget on the dashboard with a cursor of its own.

    A booking key pressed in it follows that cursor, not the period's anchor.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key(f"{RowKind.DAY}2026-06-10")
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()

        assert await prefilled(app, pilot) == (date(2026, 6, 10), AbsenceType.ANNUAL)


async def test_a_on_the_total_row_books_the_period_anchor(
    app_factory: AppFactory,
) -> None:
    """The total row belongs to no day, so the period's anchor answers for it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key("t-period")
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()

        when, _ = await prefilled(app, pilot)
        assert when == dashboard(app).period.anchor


async def test_wallet_asks_the_screen_to_open_the_booking(
    app_factory: AppFactory,
) -> None:
    """The wallet asks for a type and the screen supplies the rest.

    A panel cannot push a modal, and the day, the remaining allowance and the
    TOIL balance all come from the screen.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.screen.query_one(WalletModule).post_message(BookRequested(AbsenceType.SICK))
        await pilot.pause()

        assert await prefilled(app, pilot) == (
            dashboard(app).period.anchor,
            AbsenceType.SICK,
        )


# --- deleting from a row --------------------------------------------------


async def test_x_on_a_booking_asks_before_it_removes_it(
    app_factory: AppFactory,
) -> None:
    """The question names the type and the day, so agreeing to it is specific."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        toil = date(2026, 6, 12)  # the seed's TOIL day
        widget.toggle(f"{RowKind.DAY}{toil}")
        await pilot.pause()
        widget.focus_key(absence_key(app, toil))
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()
        showing(app, ConfirmModal)
        asked = screen_text(app)
        assert "Fri 12 Jun" in asked, asked
        assert "TOIL" in asked, "the question names the type as well as the day"

        await pilot.press("enter")
        await pilot.pause()

        booked = app.services.absence.in_range(date(2026, 6, 8), date(2026, 6, 14))
        assert [row.date for row in booked] == [date(2026, 6, 9)], "the TOIL day stayed"
        assert "removed" in status_text(app)


async def test_declining_the_question_leaves_the_booking_alone(
    app_factory: AppFactory,
) -> None:
    """Escape on a confirmation is an answer, and the answer is no."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        toil = date(2026, 6, 12)
        widget.toggle(f"{RowKind.DAY}{toil}")
        await pilot.pause()
        widget.focus_key(absence_key(app, toil))
        await pilot.pause()
        before = app.services.absence.in_range(date(2026, 6, 8), date(2026, 6, 14))

        await pilot.press("x")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        after = app.services.absence.in_range(date(2026, 6, 8), date(2026, 6, 14))
        assert [row.date for row in after] == [row.date for row in before]


async def test_booking_changed_under_the_modal_is_kept(
    app_factory: AppFactory,
) -> None:
    """The accepted question identifies the row it described, not its slot."""
    app = app_factory()
    toil = date(2026, 6, 12)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.toggle(f"{RowKind.DAY}{toil}")
        await pilot.pause()
        widget.focus_key(absence_key(app, toil))
        await pilot.press("x")
        await pilot.pause()
        showing(app, ConfirmModal)

        replacement = app.services.absence.in_range(toil, toil)[0]
        replacement.absence_type = AbsenceType.SICK
        app._session.commit()
        await pilot.press("enter")
        await pilot.pause()

        assert [
            row.absence_type for row in app.services.absence.in_range(toil, toil)
        ] == [AbsenceType.SICK]
        assert status_text(app) == PLAN_CHANGED


async def test_x_on_a_worked_day_points_at_what_it_can_act_on(
    app_factory: AppFactory,
) -> None:
    """The key is offered on every row, so every row owes it an answer."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key(f"{RowKind.DAY}2026-06-10")
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()

        assert status_text(app) == "Select a session to void or a booking to remove"
        assert sessions_on(app._session, date(2026, 6, 10))


async def test_x_with_nothing_to_delete_says_nothing(
    app_factory: AppFactory,
) -> None:
    """The period total belongs to no day, and an empty table has no cursor."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key("t-period")
        await pilot.pause()
        quiet = status_text(app)

        await pilot.press("x")
        await pilot.pause()
        assert status_text(app) == quiet
        showing(app, DashboardScreen)

        # An empty table has no cursor, so the message carries no key at all.
        app.screen.query_one(RecordsModule).post_message(DeleteHere(None))
        await pilot.pause()
        assert status_text(app) == quiet
        showing(app, DashboardScreen)


async def test_x_on_a_booking_already_gone_says_so(
    app_factory: AppFactory,
) -> None:
    """The row is a snapshot: the id it carries can be gone by the keypress."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.screen.query_one(RecordsModule).post_message(
            DeleteHere(f"{RowKind.ABSENCE}9999")
        )
        await pilot.pause()

        assert status_text(app) == "That booking has already gone"
        showing(app, DashboardScreen)


# --- voiding a session from a row -------------------------------------------

MONDAY = date(2026, 6, 8)
"""The seed's Monday: 08:01 to 12:30, then 13:10 to 16:14."""

DAY = timedelta(hours=7, minutes=24)


async def on_session(app: FlexiApp, pilot: Pilot[None], key: str) -> None:
    """Open the day a session row belongs to, and put the cursor on the row."""
    widget = table(app)
    widget.focus()
    widget.toggle(f"{RowKind.DAY}{MONDAY}")
    await pilot.pause()
    widget.focus_key(key)
    await pilot.pause()


def morning(app: FlexiApp) -> str:
    """The row key of Monday's first session, looked up by id like a booking."""
    first = min(app.services.clock.segments_on(MONDAY), key=lambda found: found.start)
    return f"{RowKind.SESSION}{first.session_id}"


async def test_x_on_a_session_asks_before_it_voids_it(
    app_factory: AppFactory,
) -> None:
    """The question names the stretch, its day and its length, and the way back."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        key = morning(app)
        await on_session(app, pilot, key)
        worked = app.services.ledger.day(MONDAY).worked

        await pilot.press("x")
        await pilot.pause()
        assert showing(app, ConfirmModal)._question == (
            "Void 08:01 → 12:30 on Mon 8 Jun (4:29)? It stops counting; the clock "
            "record is kept. Add the real hours with n."
        )

        await pilot.press("enter")
        await pilot.pause()

        assert status_text(app) == "Voided 08:01 → 12:30 on Mon 8 Jun"
        after = app.services.ledger.day(MONDAY).worked
        assert after == worked - timedelta(hours=4, minutes=29)
        assert key not in [row.key for row in table(app).visible_rows()]


async def test_n_after_voiding_the_afternoon_records_on_its_day(
    app_factory: AppFactory,
) -> None:
    """The way to fix a session: void it with `x`, then add the real hours with `n`.

    The afternoon takes the lunch break above it when it goes, so the slot the
    cursor was in holds Tuesday, and `n` follows the cursor.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        last = max(
            app.services.clock.segments_on(MONDAY), key=lambda found: found.start
        )
        await on_session(app, pilot, f"{RowKind.SESSION}{last.session_id}")

        await pilot.press("x")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert status_text(app) == "Voided 13:10 → 16:14 on Mon 8 Jun"

        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()

        assert showing(app, CorrectionModal)._day == MONDAY


async def test_declining_the_question_leaves_the_session_alone(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await on_session(app, pilot, morning(app))
        before = app.services.clock.segments_on(MONDAY)

        await pilot.press("x")
        await pilot.pause()
        showing(app, ConfirmModal)
        await pilot.press("escape")
        await pilot.pause()

        assert app.services.clock.segments_on(MONDAY) == before


@pytest.mark.parametrize(
    "settled_on", [MONDAY, date(2026, 6, 10)], ids=["that-day", "a-later-day"]
)
async def test_the_question_warns_when_the_day_is_settled(
    app_factory: AppFactory, settled_on: date
) -> None:
    """A settlement is a fixed amount: voiding a day under it moves it off zero.

    Settling to yesterday, then voiding an earlier day's forgotten clock-out, is
    the usual order, so a line drawn after the day covers it as one on it does.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert zero_balance(app.services, settled_on).success
        await on_session(app, pilot, morning(app))

        await pilot.press("x")
        await pilot.pause()

        assert showing(app, ConfirmModal)._question.endswith(
            "Add the real hours with n. If you settled the balance to zero on or "
            "after this day, it will no longer read zero."
        )


async def test_a_day_no_settlement_covers_carries_no_warning(
    app_factory: AppFactory,
) -> None:
    """One drawn the day before, and one in the next leave year, which starts afresh."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert zero_balance(app.services, MONDAY - timedelta(days=1)).success
        next_year = date(2027, 4, 6)
        assert app.services.adjustments.record(next_year, -DAY, "next year").success
        await on_session(app, pilot, morning(app))

        await pilot.press("x")
        await pilot.pause()

        assert "settled" not in showing(app, ConfirmModal)._question


async def test_a_balance_brought_in_today_carries_no_warning(
    app_factory: AppFactory,
) -> None:
    """Dated the day it was made, an adjustment settled nothing.

    Only a finished day is settled, so an opening balance brought in today is a
    later row in the leave year that voiding Monday cannot move off zero.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        brought = timedelta(hours=5, minutes=30)
        assert adjust_balance(app.services, brought, "Brought forward").success
        await on_session(app, pilot, morning(app))

        await pilot.press("x")
        await pilot.pause()

        assert "settled" not in showing(app, ConfirmModal)._question


async def test_x_on_the_running_session_says_to_clock_out_first(
    app_factory: AppFactory,
) -> None:
    """Its end is not known yet, so there is nothing to ask about."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        running = app.services.clock.get_open_session()
        assert running is not None, "the seed leaves this afternoon running"
        widget = table(app)
        widget.focus()
        widget.toggle(f"{RowKind.DAY}{running.work_date}")
        await pilot.pause()
        widget.focus_key(f"{RowKind.SESSION}{running.id}")
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()

        showing(app, DashboardScreen)
        assert status_text(app) == "Clock out first; a running session cannot be voided"
        assert app.services.clock.is_clocked_in()


async def test_x_on_a_break_points_at_what_it_can_act_on(
    app_factory: AppFactory,
) -> None:
    """A break is keyed after the session before it, and is not that session."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        before = app.services.clock.segments_on(MONDAY)
        await on_session(app, pilot, f"{morning(app)}-break")

        await pilot.press("x")
        await pilot.pause()

        showing(app, DashboardScreen)
        assert status_text(app) == "Select a session to void or a booking to remove"
        assert app.services.clock.segments_on(MONDAY) == before


async def test_x_on_a_session_already_gone_says_so(
    app_factory: AppFactory,
) -> None:
    """The row is a snapshot: the session it names can be voided elsewhere."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.screen.query_one(RecordsModule).post_message(
            DeleteHere(f"{RowKind.SESSION}9999")
        )
        await pilot.pause()

        assert status_text(app) == "That session has already gone"
        showing(app, DashboardScreen)
