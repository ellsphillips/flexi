"""Adjust balance…: bringing a balance in, or correcting it, without a shell.

The palette entry runs `DashboardScreen.action_adjust_balance`, which these
tests call directly; tests/tui/test_provider.py chooses it from the palette.
"""

from __future__ import annotations

from datetime import date

from textual.pilot import Pilot
from textual.widgets import Digits, Input, Static

from flexi.app import FlexiApp
from flexi.domain.format import digits as digits_of
from flexi.screens.modals import AdjustmentModal
from tests.tui.conftest import WIDE, AppFactory, dashboard, showing, status_text

TODAY = date(2026, 6, 11)
"""The Thursday the frozen clock is standing on."""


async def adjust(
    app: FlexiApp, pilot: Pilot[None], amount: str, reason: str
) -> AdjustmentModal:
    """Open the modal, fill it in, and confirm it."""
    dashboard(app).action_adjust_balance()
    await pilot.pause()
    modal = showing(app, AdjustmentModal)
    modal.query_one("#adjustment-amount", Input).value = amount
    modal.query_one("#adjustment-reason", Input).value = reason
    await pilot.press("enter")
    await pilot.pause()
    await pilot.pause()
    return modal


def error_on(modal: AdjustmentModal) -> str:
    return str(modal.query_one("#modal-error", Static).render())


async def test_adjustment_counts_from_today_and_moves_the_balance(
    app_factory: AppFactory,
) -> None:
    """Read off the widget: the arithmetic and the redraw are two separate things."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        readout = app.screen.query_one("#balance-digits", Digits)
        before = readout.value

        await adjust(app, pilot, "+5:30", "Brought forward")

        [row] = app.services.adjustments.all()
        assert (row.date, row.minutes, row.reason) == (TODAY, 330, "Brought forward")
        assert "Balance adjusted by +5:30" in status_text(app)
        assert readout.value != before, "the readout still shows the old balance"
        now = dashboard(app).now
        shown = app.services.ledger.balance(TODAY, now=now).as_shown()
        assert readout.value == digits_of(shown.delta)


async def test_the_minus_flexi_prints_takes_time_off(app_factory: AppFactory) -> None:
    """U+2212 is what the screen draws, so it is what gets pasted back."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        await adjust(app, pilot, "\N{MINUS SIGN}1:30", "Left early on Monday")

        [row] = app.services.adjustments.all()
        assert row.minutes == -90


async def test_the_modal_names_the_day_it_counts_from(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        dashboard(app).action_adjust_balance()
        await pilot.pause()

        modal = showing(app, AdjustmentModal)
        assert "Thu 11 Jun" in modal.modal_title
        assert app.focused is modal.query_one("#adjustment-amount", Input)


async def test_unreadable_amount_keeps_the_modal_open(app_factory: AppFactory) -> None:
    """The refusal is the command line's, and what was typed stays on screen."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        modal = await adjust(app, pilot, "5", "Brought forward")

        assert showing(app, AdjustmentModal) is modal
        assert "use H:MM" in error_on(modal)
        assert modal.query_one("#adjustment-amount", Input).value == "5"
        assert app.services.adjustments.all() == []


async def test_zero_amount_keeps_the_modal_open(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        modal = await adjust(app, pilot, "0:00", "Brought forward")

        assert showing(app, AdjustmentModal) is modal
        assert "change nothing" in error_on(modal)
        assert app.services.adjustments.all() == []


async def test_adjustment_needs_a_reason(app_factory: AppFactory) -> None:
    """`flexi balance log` reads every row back, so each one says why."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        dashboard(app).action_adjust_balance()
        await pilot.pause()
        modal = showing(app, AdjustmentModal)
        modal.query_one("#adjustment-amount", Input).value = "+5:30"
        modal.query_one("#adjustment-reason", Input).value = "   "
        modal.query_one("#adjustment-reason", Input).focus()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert showing(app, AdjustmentModal) is modal
        assert error_on(modal) == "Type a reason, like Brought forward"
        assert app.services.adjustments.all() == []


async def test_empty_amount_says_what_to_type(app_factory: AppFactory) -> None:
    """Not "'' is not an amount", which quotes back nothing as if it were typed."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        modal = await adjust(app, pilot, "", "Brought forward")

        assert showing(app, AdjustmentModal) is modal
        assert error_on(modal) == "Type an amount, like +5:30 or -1:30"
        assert app.services.adjustments.all() == []


async def test_enter_after_the_amount_moves_to_the_reason(
    app_factory: AppFactory,
) -> None:
    """The amount alone is half an answer, and the reason is the half still empty."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        dashboard(app).action_adjust_balance()
        await pilot.pause()
        modal = showing(app, AdjustmentModal)
        modal.query_one("#adjustment-amount", Input).value = "+3:15"

        await pilot.press("enter")
        await pilot.pause()

        assert showing(app, AdjustmentModal) is modal
        assert app.focused is modal.query_one("#adjustment-reason", Input)
        assert error_on(modal) == ""
        assert app.services.adjustments.all() == []


async def test_examples_read_as_examples(app_factory: AppFactory) -> None:
    """`+5:30` and `Brought forward` in grey look like answers already given."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        dashboard(app).action_adjust_balance()
        await pilot.pause()

        modal = showing(app, AdjustmentModal)
        amount = modal.query_one("#adjustment-amount", Input)
        reason = modal.query_one("#adjustment-reason", Input)
        assert amount.placeholder == "e.g. +5:30"
        assert reason.placeholder == "e.g. Brought forward"


async def test_cancelling_writes_nothing(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        dashboard(app).action_adjust_balance()
        await pilot.pause()
        modal = showing(app, AdjustmentModal)
        modal.query_one("#adjustment-amount", Input).value = "+5:30"
        modal.query_one("#adjustment-reason", Input).value = "Brought forward"

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, AdjustmentModal)
        assert app.services.adjustments.all() == []
