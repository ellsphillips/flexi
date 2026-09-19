"""Feature 3: a row per day, opening to the day's breakdown."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import event
from textual.pilot import Pilot
from textual.widgets import Input, RadioSet

from flexi.app import FlexiApp
from flexi.components.expandable import (
    ExpandableTable,
    RowKind,
)
from flexi.components.modules.records import DeleteHere, RecordsModule
from flexi.components.modules.wallet import BookRequested, WalletModule
from flexi.components.progress import ProgressRail, TimeProgress
from flexi.constants import AbsenceType
from flexi.domain.format import delta
from flexi.messages import Scope
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.modals import AbsenceModal, ConfirmModal
from flexi.services.absence import PLAN_CHANGED
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
        summary = app.services.ledger.summary(board.period.start, board.period.end)
        assert after.strip() == delta(summary.delta)


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


async def test_x_on_a_worked_day_says_not_implemented(
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

        assert status_text(app) == "Deleting sessions is not implemented yet"
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
