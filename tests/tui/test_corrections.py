"""The two keys: record work that was not clocked, and read back what was.

`n` was declared in the keymap and bound to nothing at all, so the config
promised a key that did not exist. It records a correction now, and `N` reviews
them.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from textual.pilot import Pilot
from textual.widgets import Digits, Input

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


async def test_the_key_records_work_on_the_day_being_looked_at(
    app_factory: AppFactory,
) -> None:
    """It opens on the cursor, which is the day somebody has just noticed."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        before = app.services.ledger.day(when).worked

        await record(pilot, "6:00", "7:30")

        after = app.services.ledger.day(when).worked
        assert after - before == timedelta(hours=1, minutes=30)
        assert "Recorded" in status_text(app)


async def test_a_correction_is_drawn_apart_from_a_punched_session(
    app_factory: AppFactory,
) -> None:
    """The strip is where the two kinds of record sit side by side.

    Inside the drawn day window on purpose: a correction before 07:00 is a real
    thing to record and simply falls outside what the strip draws, which would
    make this pass or fail on the window rather than on the fill.
    """
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


async def test_a_refusal_is_reported_rather_than_written(
    app_factory: AppFactory,
) -> None:
    """The modal collects; the service decides. A refusal has to reach the bar."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        when = dashboard(app).period.anchor
        before = app.services.ledger.day(when).worked

        await record(pilot, "17:00", "9:00")

        assert "ends before it starts" in status_text(app)
        assert app.services.ledger.day(when).worked == before


async def test_a_time_that_cannot_be_read_keeps_the_modal_open(
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


async def test_an_empty_field_asks_for_it_rather_than_guessing(
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


async def test_the_review_key_lists_the_corrections_in_the_period(
    app_factory: AppFactory,
) -> None:
    """Read as a set, which is how somebody checks what they claimed."""
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


async def test_the_review_says_so_when_there_is_nothing_to_review(
    app_factory: AppFactory,
) -> None:
    """An empty dialog reads as one that failed to load."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        await pilot.press(CONFIG.hotkeys.corrections)
        await pilot.pause()

        showing(app, CorrectionsModal)
        assert "Nothing recorded after the fact" in screen_text(app)


async def test_the_review_closes_on_escape(app_factory: AppFactory) -> None:
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
    """The whole point of writing the morning down is the figure it feeds.

    Read off the widget rather than the service: the arithmetic being right and
    the dashboard being redrawn are two separate things, and a correction that
    lands in the database while the balance keeps its old figure is the version
    of this feature nobody would trust.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        digits = app.screen.query_one("#balance-digits", Digits)
        before = digits.value

        await record(pilot, "6:00", "7:30")

        assert digits.value != before, "the readout still shows the old balance"
        summary = app.services.ledger.balance(dashboard(app).now.date())
        assert digits.value == digits_of(summary.delta)
