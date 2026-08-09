"""Install, launch, answer five questions, and get to the dashboard.

This is the only path every single user takes, and the one nobody runs again
after their first day -- so it is the one most likely to rot unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, Input, Select

from flexi.app import FlexiApp
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.setup import SetupScreen
from flexi.services.settings import SettingsService
from tests.tui.conftest import WIDE, showing


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """A migrated database with nothing in it, as run_migrations would leave it."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    Base.metadata.create_all(engine)
    engine.dispose()
    return path


async def _answer(app: FlexiApp, working_days: str) -> None:
    screen = showing(app, SetupScreen)
    screen.query_one("#input-leave-start", Input).value = "04-06"
    screen.query_one("#input-entitlement", Input).value = "28"
    screen.query_one("#input-working-days", Input).value = working_days
    screen.query_one("#select-division", Select).value = "scotland"
    screen.query_one("#input-auto-close", Input).value = "18:30"


async def test_a_fresh_database_opens_on_setup(fresh_db: Path) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SetupScreen)


@pytest.mark.parametrize("working_days", ["Mon-Fri", "0,1,2,3,4", "mon, tue, wed"])
async def test_setup_accepts_a_reasonable_answer_and_lands_on_the_dashboard(
    fresh_db: Path, working_days: str
) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, working_days)
        await pilot.pause()
        showing(app, SetupScreen).query_one("#btn-save", Button).press()
        await pilot.pause()
        await pilot.pause()
        showing(app, DashboardScreen)


async def test_a_second_launch_goes_straight_to_the_dashboard(fresh_db: Path) -> None:
    """The regression: setup used to succeed and the next launch to raise."""
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Mon-Fri")
        await pilot.pause()
        showing(app, SetupScreen).query_one("#btn-save", Button).press()
        await pilot.pause()
        await pilot.pause()

    again = FlexiApp(db_path=fresh_db)
    async with again.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(again, DashboardScreen)


async def test_setup_refuses_an_answer_it_cannot_read(fresh_db: Path) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "whenever")
        await pilot.pause()
        showing(app, SetupScreen).query_one("#btn-save", Button).press()
        await pilot.pause()

        showing(app, SetupScreen)  # still here, not dismissed

    with get_session(create_db_engine(fresh_db)) as session:
        assert SettingsService(session).get_settings() is None


async def test_what_was_answered_is_what_was_saved(fresh_db: Path) -> None:
    app = FlexiApp(db_path=fresh_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _answer(app, "Tue-Thu")
        await pilot.pause()
        showing(app, SetupScreen).query_one("#btn-save", Button).press()
        await pilot.pause()
        await pilot.pause()

    with get_session(create_db_engine(fresh_db)) as session:
        settings = SettingsService(session)
        stored = settings.get_settings()
        assert stored is not None
        assert stored.leave_year_start == "04-06"
        assert stored.bank_holiday_division == "scotland"
        assert stored.auto_close_time == "18:30"
        assert settings.get_working_day_indices() == [1, 2, 3]
        assert settings.get_active_entitlement_days(None) == 28.0
