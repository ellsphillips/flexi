"""The application shell: how it opens, what it says, and where it will not go.

The dashboard is the interesting screen, so the shell around it is the part that
gets tested by hand once and then never again: the first launch, the launch that
was declined, the version check, and every destination reached before there is a
dashboard to reach it from. Those are exactly the paths a user meets on their
worst day — a fresh install, an empty database, no network — so they are the
ones worth pinning.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import update
from textual.widgets import Input, Select

from flexi.app import FlexiApp
from flexi.components.modules.records import RecordsModule
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import BankHolidayCache, Base
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.leave import LeaveScreen
from flexi.screens.settings import SettingsScreen
from flexi.screens.setup import SetupScreen
from flexi.services.registry import Services
from flexi.services.samples import NOW
from tests.tui.conftest import WIDE, AppFactory, dashboard, showing

TODAY = date(2026, 6, 11)
"""The Thursday the frozen clock is standing on."""


def said(app: FlexiApp) -> list[str]:
    """Every notification the application has raised, oldest first."""
    return [notification.message for notification in app._notifications]


@pytest.fixture
def unconfigured(tmp_path: Path) -> Path:
    """A migrated database nobody has answered the setup questions for."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    Base.metadata.create_all(engine)
    engine.dispose()
    return path


# -- opening ---------------------------------------------------------------


async def test_setup_can_hand_straight_over_to_the_settings_screen(
    app_factory: AppFactory,
) -> None:
    """`flexi init` on an already-configured install offers to change them.

    The answer is acted on here rather than in the CLI because the CLI has no
    screen to push onto; it sets a flag and the application honours it as it
    mounts. A flag nobody reads is indistinguishable from the answer "no".
    """
    app = app_factory()
    app.open_settings = True
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SettingsScreen)
        assert app._dashboard() is not None, "settings should open over the dashboard"


async def test_declining_setup_closes_the_application(unconfigured: Path) -> None:
    """Escape on the first screen means "not now", not "start anyway".

    There is nothing behind the setup screen — no dashboard is pushed until the
    questions are answered — so a cancel that merely dismissed would leave a
    blank terminal and no way out.
    """
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SetupScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert app.return_code == 0, "escape should close the application, cleanly"
        assert app.services.settings.get_settings() is None, "nothing was answered"


# -- what it tells you on the way in ---------------------------------------


async def test_an_empty_bank_holiday_calendar_is_reported_as_a_consequence(
    app_factory: AppFactory,
) -> None:
    """It says what the missing calendar will do, not that a fetch failed.

    The seed's cache is ten days old, and the suite refuses the connection, so
    this is the state a first launch on a train arrives in. Without the warning
    the only symptom is every bank holiday quietly counted as a day nobody
    worked.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert "No bank holiday calendar. Days off will count as working days." in said(
            app
        )


async def test_a_calendar_fetched_this_week_is_left_alone(seeded_db: Path) -> None:
    """A fresh cache means no round trip and nothing to report.

    Refetching on every launch would put a GOV.UK timeout in front of the
    dashboard once a day, and warning about a calendar that is present would
    train people to ignore the warning that matters.
    """
    with get_session(create_db_engine(seeded_db)) as session:
        session.execute(update(BankHolidayCache).values(fetched_at=NOW))
        session.commit()

    app = FlexiApp(db_path=seeded_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert not [line for line in said(app) if "bank holiday" in line.lower()]


async def test_a_newer_release_is_announced_with_the_command_that_installs_it(
    app_factory: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notice carries the command that acts on it.

    Telling somebody they are out of date without telling them what to run is a
    notification they have to go and research.
    """
    monkeypatch.setattr("flexi.app.available_update", lambda: "99.0.0")
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        announced = [line for line in said(app) if "Update available" in line]
        assert announced, "a newer version should be announced"
        assert "99.0.0" in announced[0]
        assert "uv tool upgrade flexi" in announced[0]


async def test_being_up_to_date_is_said_with_silence(app_factory: AppFactory) -> None:
    """`available_update` returns None here, as it does for most launches.

    A "you are up to date" toast on every single launch is noise, and noise is
    what makes the update notice that matters get dismissed unread.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert not [line for line in said(app) if "Update available" in line]


# -- navigation ------------------------------------------------------------


async def test_asking_for_the_destination_you_are_already_on_does_nothing(
    app_factory: AppFactory,
) -> None:
    """F1 on the dashboard is a key people press to check where they are.

    Without the guard it pushes a second copy of the screen it is already
    showing, and escape then appears to do nothing because there is an identical
    screen underneath.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        depth = len(app.screen_stack)

        await pilot.press("f1")
        await pilot.pause()

        assert len(app.screen_stack) == depth
        showing(app, DashboardScreen)


async def test_a_destination_that_needs_the_dashboard_says_so_when_there_is_none(
    unconfigured: Path,
) -> None:
    """A destination that cannot be drawn yet says so.

    Insights reads the dashboard's period, so before setup there is nothing for
    it to show — and a key that silently does nothing reads as a hang.
    """
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("f3")
        await pilot.pause()

        assert "Insights is not built yet." in said(app)
        showing(app, SetupScreen)


async def test_the_clock_key_does_nothing_until_there_is_somewhere_to_clock(
    unconfigured: Path,
) -> None:
    """The key reaches every screen, including one that must not write.

    The clock binding is application-wide and `priority=True`, so it fires on
    the setup screen too — where clocking in would write a session against a
    working pattern nobody has chosen yet.
    """
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.set_focus(None)  # the slash reaches a focused field as a character

        await pilot.press("slash")
        await pilot.pause()

        assert app.services.clock.get_sessions_for_date(TODAY) == []
        showing(app, SetupScreen)


async def test_settings_saved_before_setup_has_finished_are_still_written(
    unconfigured: Path,
) -> None:
    """F4 works everywhere, including where there is no dashboard to redraw.

    Saving rebuilds the services and then refreshes the dashboard with them.
    On this path there is no dashboard, and reaching for one is how a defensive
    `if` earns its keep: the answers still have to reach the database.
    """
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("f4")
        await pilot.pause()
        showing(app, SettingsScreen)

        await pilot.click("#btn-save")
        await pilot.pause()

        showing(app, SetupScreen)
        stored = Services.build(app._session).settings.get_settings()
        assert stored is not None
        assert stored.auto_close_time == "18:00"


async def test_the_clock_runs_on_the_screen_that_owns_it_from_anywhere(
    app_factory: AppFactory,
) -> None:
    """One key, from any screen, always recorded against the dashboard.

    The binding is application-wide but the confirmation, the status line and
    the redraw all belong to the dashboard, so the app hands the press down
    rather than talking to the clock service itself.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")  # insights, with the dashboard underneath
        await pilot.pause()
        assert app.services.clock.is_clocked_in()

        await pilot.press("slash")
        await pilot.pause()

        assert not app.services.clock.is_clocked_in()


async def test_the_dashboard_opens_with_services_built_from_the_setup_answers(
    unconfigured: Path,
) -> None:
    """The service graph is wired before the answers exist, so it is rewired.

    The bank-holiday division is read once, when the graph is built, and never
    again. A dashboard handed the graph that was constructed at launch would ask
    GOV.UK for the English calendar for the rest of a Scottish user's working
    life, and quietly count their holidays as days they failed to work.
    """
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = showing(app, SetupScreen)
        screen.query_one("#input-leave-start", Input).value = "04-06"
        screen.query_one("#input-entitlement", Input).value = "28"
        screen.query_one("#input-working-days", Input).value = "Tue-Thu"
        screen.query_one("#select-division", Select).value = "scotland"
        screen.query_one("#input-auto-close", Input).value = "18:30"
        await pilot.pause()

        screen.action_save()
        await pilot.pause()
        await pilot.pause()

        showing(app, DashboardScreen)
        assert app.services.bank_holidays._division == "scotland"


async def test_the_leave_year_opens_on_the_day_the_dashboard_was_showing(
    app_factory: AppFactory,
) -> None:
    """F2 hands the leave screen the dashboard's anchor rather than today.

    Somebody browsing next year's dates on the dashboard and then pressing F2 is
    asking about next year's allowance. Opening on the current leave year would
    answer a question they did not ask, and quietly against the wrong
    entitlement.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("y", "right_square_bracket")  # a year on, a leave year on
        await pilot.pause()
        anchor = dashboard(app).period.anchor

        await pilot.press("f2")
        await pilot.pause()

        screen = showing(app, LeaveScreen)
        assert app.nav == "leave"
        assert screen.period.anchor == anchor
        assert not screen.period.contains(TODAY), "that is this year's leave, not next"


async def test_escaping_the_leave_year_returns_the_bar_to_the_dashboard(
    app_factory: AppFactory,
) -> None:
    """Leaving a pushed screen has to put the navigation label back.

    A bar still reading "Leave" over a dashboard is a label that has stopped
    describing anything, and the next F2 would be refused as a move to where
    you already are.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        showing(app, LeaveScreen)

        await pilot.press("escape")
        await pilot.pause()

        showing(app, DashboardScreen)
        assert app.nav == "dashboard"


async def test_returning_to_the_dashboard_when_nothing_was_pushed_only_relabels(
    app_factory: AppFactory,
) -> None:
    """The label and the screen stack are two pieces of state, kept apart.

    `nav` is what the bar draws; `_pushed` is the screen F1 has to pop. Going
    home has to cope with the two disagreeing without popping the dashboard
    itself — the one screen there is nothing underneath.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        depth = len(app.screen_stack)
        app.nav = "leave"  # the bar says Leave; nothing was ever pushed

        await pilot.press("f1")
        await pilot.pause()

        assert app.nav == "dashboard"
        assert len(app.screen_stack) == depth
        showing(app, DashboardScreen)


# -- settings --------------------------------------------------------------


async def test_leaving_settings_without_saving_rebuilds_nothing(
    app_factory: AppFactory,
) -> None:
    """Escape means the answers on screen were never asked for.

    Rebuilding the service graph regardless would throw away every cached
    derivation on the way out of a screen somebody only opened to look at, and
    make "did I change anything?" impossible to answer from the outside.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        before = app.services

        await pilot.press("f4")
        await pilot.pause()
        showing(app, SettingsScreen)
        await pilot.press("escape")
        await pilot.pause()

        showing(app, DashboardScreen)
        assert app.services is before, "nothing was answered, so nothing was rewired"


async def test_a_saved_working_pattern_reaches_the_dashboard_without_a_relaunch(
    app_factory: AppFactory,
) -> None:
    """The screen behind the dialog is told what was saved.

    Dropping Monday changes what every figure on the dashboard is measured
    against. Without the redraw the records panel goes on reporting the week against the
    old expectation until the next keystroke, so the answer appears to have been
    ignored at exactly the moment it was given.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        before = str(app.screen.query_one(RecordsModule).border_subtitle)

        await pilot.press("f4")
        await pilot.pause()
        showing(app, SettingsScreen).query_one(
            "#input-working-days", Input
        ).value = "Tue-Fri"
        await pilot.click("#btn-save")
        await pilot.pause()

        showing(app, DashboardScreen)
        after = str(app.screen.query_one(RecordsModule).border_subtitle)
        assert after != before, f"the week is still measured against {before}"
