"""Feature 3: a row per day, opening to the day's breakdown."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import event
from textual.pilot import Pilot
from textual.widgets import Input, RadioSet

from flexi.app import FlexiApp
from flexi.components.expandable import (
    ABSENCE,
    DAY,
    SESSION,
    TOTAL,
    ExpandableTable,
)
from flexi.components.modules.records import DeleteHere, RecordsModule
from flexi.components.modules.wallet import BookRequested, WalletModule
from flexi.components.progress import ProgressRail, TimeProgress
from flexi.constants import AbsenceType
from flexi.domain.format import delta
from flexi.messages import Scope
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.modals import AbsenceModal, ConfirmModal
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
    """The row key of the booking on a day, looked up rather than counted.

    The key carries a database id. Writing one out fixes the test to the order
    the demo seed happens to insert its absences in, and the two that did broke
    the day the seed stopped booking annual leave over a bank holiday -- a
    change with nothing to do with what they assert.
    """
    booked = app.services.absence.in_range(when, when)
    assert booked, f"the seed has nothing booked on {when}"
    return f"{ABSENCE}{booked[0].id}"


async def prefilled(app: FlexiApp, pilot: Pilot[None]) -> tuple[date, AbsenceType]:
    """The day and the type the booking dialog arrived already holding.

    Read off the fields somebody is about to press enter on rather than the
    arguments the modal was constructed with: the whole promise of a pre-filled
    dialog is that what it shows is what it will book.

    Settled first, and inside the helper rather than at each call site. The
    modal chooses its type in work deferred to after its first layout, so a
    `pause` returns with the dialog mounted and nothing pressed on it -- which
    on a loaded Windows runner is what the assertion saw.
    """
    await settled(pilot)
    modal = showing(app, AbsenceModal)
    when = date.fromisoformat(modal.query_one("#absence-date", Input).value)
    pressed = modal.query_one("#absence-type", RadioSet).pressed_button
    assert pressed is not None, "the dialog opened with no type chosen"
    return when, AbsenceType(pressed.name)


async def test_a_week_is_seven_rows_and_a_total(app_factory: AppFactory) -> None:
    """It shows every day in the period, worked or not, plus the period line."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        rows = table(app).visible_rows()
        assert len([row for row in rows if row.kind == DAY]) == 7
        assert rows[-1].key == "t-period"


async def test_space_opens_the_day_under_the_cursor(app_factory: AppFactory) -> None:
    """It reveals the sessions that produced the figures on the row."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key(f"{DAY}2026-06-08")
        await pilot.pause()

        before = len(widget.visible_rows())
        await pilot.press("space")
        await pilot.pause()

        assert len(widget.visible_rows()) > before
        assert any(row.key.startswith(SESSION) for row in widget.visible_rows())


async def test_expanding_does_not_move_the_cursor(app_factory: AppFactory) -> None:
    """It restores the cursor by key, so rows inserted above do not shift it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        target = f"{DAY}2026-06-10"
        widget.focus_key(target)
        await pilot.pause()
        assert widget.cursor_key == target

        widget.toggle(f"{DAY}2026-06-08")  # a row above the cursor
        await pilot.pause()
        assert widget.cursor_key == target


async def test_a_day_with_nothing_recorded_does_not_open(
    app_factory: AppFactory,
) -> None:
    """It leaves `space` inert where there is nothing behind the row."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        saturday = f"{DAY}2026-06-13"
        assert widget.toggle(saturday) is False
        assert saturday not in widget.expanded


async def test_shift_space_opens_and_closes_everything(app_factory: AppFactory) -> None:
    """It inverts the majority, so one key always does the visible thing."""
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


async def test_loading_a_period_costs_the_same_whatever_its_length(
    app_factory: AppFactory,
) -> None:
    """It reads a period in a fixed number of queries, not one per day.

    Asserting a *constant* rather than a literal count is the property that
    matters and the one that survives a new lookup being added: the v1 shape
    issued a query per day per concern, so a month cost thirty-one times a week.
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

            engine = services.session.get_bind()
            event.listen(engine, "before_cursor_execute", record)
            try:
                services.ledger.invalidate()
                services.ledger.days(first, first + timedelta(days=days - 1))
            finally:
                event.remove(engine, "before_cursor_execute", record)
            return len(statements)

        assert count_queries(28) == count_queries(7)


async def test_the_rails_say_how_far_through_the_day_and_the_period(
    app_factory: AppFactory,
) -> None:
    """It answers 'am I nearly done' without reading a table."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        rails = app.screen.query_one(TimeProgress)
        day = rails.query_one("#rail-day", ProgressRail)
        period = rails.query_one("#rail-period", ProgressRail)

        assert day.label == "TODAY"
        assert 0.0 < day.share < 1.0, "the seed's today is part-worked"
        assert period.label == "WEEK"
        assert period.share > 1.0, "the seed's week is over its expected hours"


async def test_the_period_rail_follows_the_granularity(app_factory: AppFactory) -> None:
    """It relabels itself rather than always saying WEEK."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("m")
        await pilot.pause()
        assert app.screen.query_one("#rail-period", ProgressRail).label == "MONTH"


async def test_the_period_rail_gives_way_when_there_is_no_room(
    app_factory: AppFactory,
) -> None:
    """Below 100 columns two rails leave each other no bar, so one goes."""
    app = app_factory()
    async with app.run_test(size=(84, 28)) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#rail-day", ProgressRail).display is True
        assert app.screen.query_one("#rail-period", ProgressRail).display is False


async def test_the_period_total_is_in_the_border_subtitle(
    app_factory: AppFactory,
) -> None:
    """It puts the period's figures in the module's live slot."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        subtitle = str(app.screen.query_one(RecordsModule).border_subtitle)
        assert " of " in subtitle


async def test_the_period_total_counts_a_correction_as_the_wallet_does(
    app_factory: AppFactory,
) -> None:
    """Two panels on one screen, showing one span, must agree about it.

    The total row summed `worked - expected - toil` by hand and dropped the
    adjustment term `BalanceSummary.delta` carries, so a recorded correction
    moved the wallet's figure and left the table's alone. Both accumulate the
    same ledgers through the domain now.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        board = showing(app, DashboardScreen)
        before = str(table(app).get_row(f"{TOTAL}period")[3])

        app.services.adjustments.record(
            board.period.anchor, timedelta(hours=3), "carried over"
        )
        board.refresh_modules(Scope.ALL)
        await pilot.pause()

        after = str(table(app).get_row(f"{TOTAL}period")[3])
        assert after != before, f"the correction never reached the total ({before})"
        summary = app.services.ledger.summary(board.period.start, board.period.end)
        assert after.strip() == delta(summary.delta)


# -- booking from a row ----------------------------------------------------


async def test_a_books_an_absence_on_the_day_under_the_cursor(
    app_factory: AppFactory,
) -> None:
    """The row you are looking at is the day you mean.

    The table is the only place on the dashboard with a cursor of its own, so a
    booking key pressed in it has to follow that cursor rather than the period's
    anchor — otherwise browsing down to Wednesday and pressing `a` books
    Thursday, and the receipt is the first anyone hears of it.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key(f"{DAY}2026-06-10")
        await pilot.pause()

        await pilot.press("a")
        await pilot.pause()

        assert await prefilled(app, pilot) == (date(2026, 6, 10), AbsenceType.ANNUAL)


async def test_a_on_a_row_that_names_no_day_falls_back_to_the_period(
    app_factory: AppFactory,
) -> None:
    """The last row of the table is the period's total, and belongs to no day.

    A cursor parked there still has to answer "book what, when": the period's
    anchor is the same day every other key on the screen would have used, which
    makes the fallback the one answer that cannot surprise anybody.
    """
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


async def test_the_wallet_asks_the_screen_to_open_the_booking(
    app_factory: AppFactory,
) -> None:
    """A panel cannot push a modal, and should not know the leave figures.

    The wallet asks for a type and the screen supplies the day, the remaining
    allowance and the TOIL balance the dialog needs. A panel that pushed its own
    modal would have to fetch all three again, and could disagree with the
    gauges it is drawn beside.
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


# -- deleting from a row ---------------------------------------------------


async def test_x_on_a_booking_asks_before_it_removes_it(
    app_factory: AppFactory,
) -> None:
    """Leave is the one thing on this screen that a keystroke can destroy.

    A clock event can be corrected by clocking again; a day of annual leave that
    vanishes on a mistyped key is an entitlement somebody has to notice is
    missing. The question names the type and the day, so agreeing to it is not
    agreeing to whatever the cursor happened to be on.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        toil = date(2026, 6, 12)  # the seed's TOIL day
        widget.toggle(f"{DAY}{toil}")
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
    """Escape on a confirmation is an answer, and the answer is no.

    A dialog that removed the day whatever you pressed would be worse than no
    dialog at all, because it teaches people to press escape and believe it.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        toil = date(2026, 6, 12)
        widget.toggle(f"{DAY}{toil}")
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


async def test_x_on_a_worked_day_says_sessions_cannot_be_deleted_yet(
    app_factory: AppFactory,
) -> None:
    """The key is offered on every row, so it owes an answer on every row.

    Deleting a session is not built. Saying so on the status bar is the
    difference between a feature that is missing and a key that is broken.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key(f"{DAY}2026-06-10")
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()

        assert status_text(app) == "Deleting sessions is not implemented yet"
        assert sessions_on(app.services.session, date(2026, 6, 10))


async def test_x_where_there_is_nothing_to_delete_says_nothing(
    app_factory: AppFactory,
) -> None:
    """Some rows carry no record, and the key owes them silence.

    The period total belongs to no day, and a table with no rows has no cursor
    at all. Neither is a failure worth a message — a status line that reports every key
    that did not apply is one nobody reads when it reports something that did.
    """
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


async def test_x_on_a_booking_that_has_already_gone_says_so(
    app_factory: AppFactory,
) -> None:
    """The row is a snapshot and the database is not.

    The key carries the id the row was drawn with, which the same booking
    removed in another window — or by the CLI in another terminal — no longer
    answers to. Confirming a removal and then failing to find it is how a
    "removed" receipt gets printed for a booking that is still there.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.screen.query_one(RecordsModule).post_message(DeleteHere(f"{ABSENCE}9999"))
        await pilot.pause()

        assert status_text(app) == "That booking has already gone"
        showing(app, DashboardScreen)
