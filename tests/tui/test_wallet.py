"""Feature 2: the wallet displays every allowance, and transacts them."""

from __future__ import annotations

from datetime import date

import pytest
from textual.widgets import Input, RadioSet

from flexi.components.common import Gauge
from flexi.constants import AbsenceType, Portion
from flexi.screens.modals import AbsenceModal
from tests.tui.conftest import WIDE, status_text

pytestmark = pytest.mark.usefixtures("_frozen")


async def test_every_allowance_has_a_gauge(app_factory) -> None:
    """It has a line for each type, whether or not anything is in it."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        for kind in AbsenceType:
            assert app.screen.query_one(f"#gauge-{kind.token}", Gauge)


async def test_a_type_with_nothing_recorded_is_not_drawn(app_factory) -> None:
    """It hides an empty uncapped allowance rather than saying 'none' five times."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#gauge-unpaid", Gauge).display is False
        assert app.screen.query_one("#gauge-sick", Gauge).display is True


async def test_a_shifted_key_opens_the_booking_modal_prefilled(app_factory) -> None:
    """It books from anywhere on the dashboard, with the type already chosen."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("S")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, AbsenceModal)
        pressed = modal.query_one("#absence-type", RadioSet).pressed_button
        assert pressed is not None
        assert pressed.name == AbsenceType.SICK.value


async def test_booking_a_half_day_draws_down_a_half(app_factory) -> None:
    """It records a morning and spends half a day of the allowance."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        before = app.services.absence.get_remaining_annual_leave(date(2026, 6, 11))
        await pilot.press("A")
        await pilot.pause()

        app.screen.query_one("#absence-date", Input).value = "2026-06-22"
        portions = app.screen.query_one("#absence-portion", RadioSet)
        portions.query("RadioButton")[1].value = True  # morning
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        booked = app.services.absence.for_date(date(2026, 6, 22))
        assert [item.portion for item in booked] == [Portion.AM]
        after = app.services.absence.get_remaining_annual_leave(date(2026, 6, 11))
        assert before is not None and after is not None
        assert before - after == 0.5


async def test_booking_over_a_bank_holiday_is_refused_with_a_reason(
    app_factory,
) -> None:
    """It says why, on the status bar, rather than failing silently."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("A")
        await pilot.pause()
        app.screen.query_one("#absence-date", Input).value = "2026-08-31"
        await pilot.press("enter")
        await pilot.pause()
        assert "bank holiday" in status_text(app).lower()


async def test_other_absence_insists_on_a_note(app_factory) -> None:
    """It refuses inside the modal, keeping what was typed on screen."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("O")
        await pilot.pause()
        modal = app.screen
        app.screen.query_one("#absence-date", Input).value = "2026-06-22"
        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is modal, "the modal should still be open"
        assert "note" in str(app.screen.query_one("#modal-error").render()).lower()


async def test_taking_toil_beyond_the_balance_warns_but_proceeds(app_factory) -> None:
    """It lets you overdraw your own arithmetic, and says that you did."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        # Spend the balance down first so the next TOIL day overdraws it.
        for offset, day in enumerate(("2026-06-22", "2026-06-23", "2026-06-24")):
            del offset
            await pilot.press("T")
            await pilot.pause()
            app.screen.query_one("#absence-date", Input).value = day
            await pilot.press("enter")
            await pilot.pause()

        assert "deficit" in status_text(app).lower()
        assert len(app.services.absence.for_date(date(2026, 6, 24))) == 1


async def test_the_modal_cancels_on_escape(app_factory) -> None:
    """It dismisses with nothing, like every modal in the application."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("A")
        await pilot.pause()
        assert isinstance(app.screen, AbsenceModal)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, AbsenceModal)
        assert app.services.absence.for_date(date(2026, 6, 11)) == []
