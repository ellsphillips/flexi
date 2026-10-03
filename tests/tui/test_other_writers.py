"""Writes made by another process while the application is open.

`flexi clock in` in a second terminal, a logon script, another copy of Flexi:
the open screens follow within a poll, `t` catches up at once, and `/` never
does the opposite of what the clock panel shows.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import Button

from flexi.app import FlexiApp
from flexi.components.common import Gauge
from flexi.constants import AbsenceType
from flexi.screens.leave import LeaveScreen
from flexi.services.registry import build_services
from tests.conftest import session_at
from tests.tui.conftest import WIDE, AppFactory, dashboard, showing, status_text

MONDAY_NEXT = date(2026, 6, 15)
"""A working day after the frozen Thursday, with nothing booked on it."""


def clock_button(app: FlexiApp) -> str:
    """What the dashboard's clock button offers, wherever the dashboard is."""
    return str(dashboard(app).query_one("#clock-button", Button).label)


@pytest.fixture
def unhurried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A poll too slow to fire during a test, so what a key does is the key's."""
    monkeypatch.setattr("flexi.app.OTHER_WRITERS_SECONDS", 3600.0)


async def test_a_clock_in_elsewhere_shows_within_a_poll(
    app_factory: AppFactory, seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off the clock here and on it from a shell: the panel follows, and ticks."""
    monkeypatch.setattr("flexi.app.OTHER_WRITERS_SECONDS", 0.05)
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("slash")
        await pilot.pause()
        assert clock_button(app) == "Arrive"

        with session_at(seeded_db) as other:
            assert build_services(other).clock.clock_in().success
        await pilot.pause(0.3)

        assert clock_button(app) == "Depart"
        assert dashboard(app)._tick is not None, "the session found is counted live"


async def test_an_open_leave_screen_follows_a_booking_elsewhere(
    app_factory: AppFactory, seeded_db: Path, unhurried: None
) -> None:
    """Every open screen is redrawn, not the dashboard alone."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        gauge = showing(app, LeaveScreen).query_one("#leave-gauge-annual", Gauge)
        assert gauge.readout.startswith("20.5 left")

        with session_at(seeded_db) as other:
            assert (
                build_services(other)
                .absence.book(MONDAY_NEXT, AbsenceType.ANNUAL)
                .success
            )
        app.notice_other_writers()
        await pilot.pause()

        assert gauge.readout.startswith("19.5 left")


async def test_the_app_s_own_writes_do_not_set_off_the_poll(
    app_factory: AppFactory, unhurried: None
) -> None:
    """A write made here has redrawn already; SQLite counts only other ones."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("slash")
        await pilot.pause()

        with patch.object(app, "refresh_open_screens") as redraw:
            app.notice_other_writers()

        redraw.assert_not_called()


async def test_slash_after_a_clock_in_elsewhere_does_not_clock_out(
    app_factory: AppFactory, seeded_db: Path, unhurried: None
) -> None:
    """The press was aimed at the Arrive the panel showed, not at the database.

    It redraws, says what happened, and waits for the next press to clock out.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("slash")
        await pilot.pause()
        with session_at(seeded_db) as other:
            assert build_services(other).clock.clock_in().success

        await pilot.press("slash")
        await pilot.pause()

        assert app.services.clock.is_clocked_in()
        assert status_text(app) == (
            "Clocked in elsewhere at 14:32; press again to clock out"
        )
        assert clock_button(app) == "Depart"

        await pilot.press("slash")
        await pilot.pause()

        assert not app.services.clock.is_clocked_in()


async def test_slash_after_a_clock_out_elsewhere_does_not_clock_in(
    app_factory: AppFactory, seeded_db: Path, unhurried: None
) -> None:
    """The other way round: the panel said Depart, and someone already had."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        board = dashboard(app)
        assert board._tick is not None
        board._tick.pause()  # The open session's tick would catch up on its own.
        with session_at(seeded_db) as other:
            assert build_services(other).clock.clock_out().success

        await pilot.press("slash")
        await pilot.pause()

        assert not app.services.clock.is_clocked_in()
        assert status_text(app) == "Clocked out elsewhere; press again to clock in"
        assert clock_button(app) == "Arrive"

        await pilot.press("slash")
        await pilot.pause()

        assert app.services.clock.is_clocked_in()


async def test_t_catches_up_at_once(
    app_factory: AppFactory, seeded_db: Path, unhurried: None
) -> None:
    """`t` reads everything again, so nobody has to wait for the poll."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("slash")
        await pilot.pause()
        with session_at(seeded_db) as other:
            assert build_services(other).clock.clock_in().success

        await pilot.press("t")
        await pilot.pause()

        assert clock_button(app) == "Depart"
        assert dashboard(app)._tick is not None
