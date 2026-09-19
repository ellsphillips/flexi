"""The two keys: `n` records work that was not clocked, `N` reviews it."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from textual.pilot import Pilot
from textual.widgets import Digits, Input, Static

from flexi.app import FlexiApp
from flexi.components.expandable import ExpandableTable, RowKind
from flexi.config import CONFIG
from flexi.domain.format import digits as digits_of
from flexi.domain.punch import Cell, strip
from flexi.screens.modals import CorrectionModal, CorrectionsModal
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


async def record(pilot: Pilot[None], opened: str, closed: str) -> None:
    """Open the correction modal, fill it in, and confirm."""
    await pilot.press(CONFIG.hotkeys.new_session)
    await pilot.pause()
    modal = pilot.app.screen
    modal.query_one("#correction-from", Input).value = opened
    modal.query_one("#correction-to", Input).value = closed
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()


async def test_work_is_recorded_on_the_period_anchor(
    app_factory: AppFactory,
) -> None:
    """With the table unfocused there is no cursor, so the anchor answers."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        before = app.services.ledger.day(when).worked

        await record(pilot, "6:00", "7:30")

        after = app.services.ledger.day(when).worked
        assert after - before == timedelta(hours=1, minutes=30)
        assert "Recorded" in status_text(app)


async def test_work_is_recorded_under_the_cursor(
    app_factory: AppFactory,
) -> None:
    """The modal title names the day, so it has to be the day that is written."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = date(2026, 6, 8)  # a Monday, and not the anchor
        assert dashboard(app).period.anchor != when
        before = app.services.ledger.day(when).worked

        widget = table(app)
        widget.focus()
        widget.focus_key(f"{RowKind.DAY}{when.isoformat()}")
        await pilot.pause()

        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()
        assert showing(app, CorrectionModal)._day == when

        modal = app.screen
        modal.query_one("#correction-from", Input).value = "6:00"
        modal.query_one("#correction-to", Input).value = "7:30"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        after = app.services.ledger.day(when).worked
        assert after - before == timedelta(hours=1, minutes=30)


async def test_row_with_no_day_falls_back_to_the_anchor(
    app_factory: AppFactory,
) -> None:
    """The last row is the period's total and belongs to no day."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        widget = table(app)
        widget.focus()
        widget.focus_key("t-period")
        await pilot.pause()

        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()

        assert showing(app, CorrectionModal)._day == dashboard(app).period.anchor


async def test_correction_is_drawn_apart_from_a_punch(
    app_factory: AppFactory,
) -> None:
    """The times are inside the drawn window, so the assertion turns on the fill."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        await record(pilot, "7:00", "8:00")

        ledger = app.services.ledger.day(when)
        drawn = strip(
            ledger,
            60,
            app.services.ledger.window,
            now=datetime.combine(when, time(23, 0), tzinfo=UTC),
        )

        assert Cell.AMENDED in drawn
        assert Cell.ON in drawn, "the punched session is still drawn as one"


async def test_refusal_is_reported_and_not_written(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        before = app.services.ledger.day(when).worked

        await record(pilot, "17:00", "9:00")

        assert "ends before it starts" in status_text(app)
        assert app.services.ledger.day(when).worked == before


async def test_unreadable_time_keeps_the_modal_open(
    app_factory: AppFactory,
) -> None:
    """What was typed stays on screen beside what was wrong with it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()
        modal = showing(app, CorrectionModal)
        modal.query_one("#correction-from", Input).value = "elevenish"
        modal.query_one("#correction-to", Input).value = "17:00"

        await pilot.press("enter")
        await pilot.pause()

        showing(app, CorrectionModal)
        assert "elevenish" in str(modal.query_one("#modal-error", Static).render())


async def test_time_made_of_markup_is_quoted_back(
    app_factory: AppFactory,
) -> None:
    """`[/]` is a closing tag with nothing to close, and Rich raises `MarkupError`."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()
        modal = showing(app, CorrectionModal)
        modal.query_one("#correction-from", Input).value = "[/]"
        modal.query_one("#correction-to", Input).value = "17:00"

        await pilot.press("enter")
        await pilot.pause()

        assert app._exception is None
        showing(app, CorrectionModal)
        assert "[/]" in str(modal.query_one("#modal-error", Static).render())


async def test_empty_field_is_asked_for_again(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        showing(app, CorrectionModal)


async def test_review_lists_the_corrections_in_the_period(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await record(pilot, "6:00", "7:30")

        await pilot.press(CONFIG.hotkeys.corrections)
        await pilot.pause()

        showing(app, CorrectionsModal)
        shown = screen_text(app)
        assert "06:00–07:30" in shown
        assert "1:30 recorded" in shown


async def test_review_says_when_there_is_nothing_to_review(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        await pilot.press(CONFIG.hotkeys.corrections)
        await pilot.pause()

        showing(app, CorrectionsModal)
        assert "Nothing recorded after the fact" in screen_text(app)


async def test_review_closes_on_escape(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press(CONFIG.hotkeys.corrections)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, CorrectionsModal)


async def test_cancelling_the_correction_writes_nothing(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        before = app.services.ledger.day(when).worked

        await pilot.press(CONFIG.hotkeys.new_session)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.services.ledger.day(when).worked == before


async def test_recording_a_correction_moves_the_balance_on_screen(
    app_factory: AppFactory,
) -> None:
    """Read off the widget: the arithmetic and the redraw are two separate things."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        digits = app.screen.query_one("#balance-digits", Digits)
        before = digits.value

        await record(pilot, "6:00", "7:30")

        assert digits.value != before, "the readout still shows the old balance"
        summary = app.services.ledger.balance(dashboard(app).now.date())
        assert digits.value == digits_of(summary.delta)
