"""Install, launch, answer five questions, and get to the dashboard.

This is the only path every single user takes, and the one nobody runs again
after their first day -- so it is the one most likely to rot unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, Input, Select

from flexi.app import FlexiApp
from flexi.components import splash as animation
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.setup import SetupScreen
from flexi.screens.splash import FRAME_SECONDS, SplashScreen
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


async def test_the_splash_lifts_and_setup_can_still_be_completed(
    fresh_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What `flexi init` does on a new machine, animation and all.

    The splash was pushed onto the stack *before* the setup form, and
    `Screen.dismiss` pops whatever is on top rather than the screen it is
    called on. So when the animation ended it deleted the form and left its own
    last frame on screen: no fields, no way forward, and "Setup was not
    completed" on quit. Every install was blocked, and the only test of the
    splash pushed it on its own, where there was nothing underneath to delete.
    """
    monkeypatch.setattr("flexi.screens.splash.wanted", lambda **_: True)

    app = FlexiApp(db_path=fresh_db)
    app.show_splash = True
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        splash = showing(app, SplashScreen)
        assert isinstance(app.screen_stack[-2], SetupScreen), (
            "the form waits underneath while the animation plays"
        )

        for _ in range(int(animation.DURATION / FRAME_SECONDS) + 5):
            splash._tick()
        await pilot.pause()

        showing(app, SetupScreen)
        await _answer(app, "Mon-Fri")
        await pilot.pause()
        showing(app, SetupScreen).query_one("#btn-save", Button).press()
        await pilot.pause()
        await pilot.pause()
        showing(app, DashboardScreen)

    with get_session(create_db_engine(fresh_db)) as session:
        stored = SettingsService(session).get_settings()
        assert stored is not None, "the answers survived the animation"
        assert stored.leave_year_start == "04-06"
