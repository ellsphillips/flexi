"""The application shell: how it opens, what it says, and where it will not go.

The dashboard is the interesting screen, so the shell around it is the part that
gets tested by hand once and then never again: the first launch, the launch that
was declined, the version check, and every destination reached before there is a
dashboard to reach it from. Those are exactly the paths a user meets on their
worst day — a fresh install, an empty database, no network — so they are the
ones worth pinning.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import update
from textual.css.query import NoMatches
from textual.pilot import Pilot
from textual.widgets import Input, Select

from flexi.app import FlexiApp
from flexi.components.chrome import NavBar
from flexi.components.modules.records import RecordsModule
from flexi.constants import Division
from flexi.models.database.db import BankHolidayRefresh, Base
from flexi.models.database.engine import create_db_engine, get_session
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.insights import InsightsScreen
from flexi.screens.leave import LeaveScreen
from flexi.screens.settings import SettingsScreen
from flexi.screens.setup import SetupScreen
from flexi.services.bank_holidays import CACHE_MAX_AGE
from flexi.services.registry import build_services
from flexi.services.samples import NOW
from tests.conftest import sessions_on
from tests.tui.conftest import WIDE, AppFactory, dashboard, showing

TODAY = date(2026, 6, 11)
"""The Thursday the frozen clock is standing on."""


async def said(app: FlexiApp, pilot: Pilot[None]) -> list[str]:
    """Every notification the application has raised, oldest first.

    Waits for the workers first. Both notices on this screen are raised from
    `@work(thread=True)` -- the update check and the bank holiday refresh -- and
    a thread is not a message, so `pilot.pause()` has nothing of theirs to
    drain. On a laptop the thread had always finished by the time the assertion
    ran; on a loaded Windows runner it had not, and the list was empty. A test
    that reads an empty list is worse than a slow one, because the two tests
    below assert that nothing was said.
    """
    await app.workers.wait_for_complete()
    await pilot.pause()
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
        assert app.dashboard() is not None, "settings should open over the dashboard"


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
    seeded_db: Path,
) -> None:
    """It says what the missing calendar will do, not that a fetch failed.

    A stale cache and no connection is the state a first launch on a train
    arrives in. Without the warning the only symptom is every bank holiday
    quietly counted as a day nobody worked.

    The staleness is arranged here rather than inherited from the seed, which
    used to carry a fixed `fetched_at` that happened to be ten days before the
    frozen clock. Two tests then read as a matched pair -- one ageing the cache,
    one not -- while only one of them said what it depended on, and the demo
    paid for it: `flexi --demo` reached for GOV.UK on every launch and warned
    about a calendar it had seeded itself.
    """
    stale = NOW - CACHE_MAX_AGE - timedelta(days=1)
    with get_session(create_db_engine(seeded_db)) as session:
        session.execute(update(BankHolidayRefresh).values(fetched_at=stale))
        session.commit()

    app = FlexiApp(db_path=seeded_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert (
            "No bank holiday calendar. Days off will count as working days."
            in await said(app, pilot)
        )


async def test_a_calendar_fetched_this_week_is_left_alone(seeded_db: Path) -> None:
    """A fresh cache means no round trip and nothing to report.

    Refetching on every launch would put a GOV.UK timeout in front of the
    dashboard once a day, and warning about a calendar that is present would
    train people to ignore the warning that matters.
    """
    with get_session(create_db_engine(seeded_db)) as session:
        session.execute(update(BankHolidayRefresh).values(fetched_at=NOW))
        session.commit()

    app = FlexiApp(db_path=seeded_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert not [
            line for line in await said(app, pilot) if "bank holiday" in line.lower()
        ]


MOUNT_TICKS = 20
"""Event loop turns to redraw across. The window this is about opens four or
five turns in, so this clears it with room to spare and costs a second."""


async def test_a_redraw_arriving_while_the_screen_mounts_is_not_a_crash(
    seeded_db: Path,
) -> None:
    """Widgets compose depth by depth, and a redraw can land between two levels.

    `refresh_open_screens` is called from off the message loop when the bank
    holiday worker finishes, so it can reach a dashboard whose modules are in
    the tree and whose calendar cells are not yet — `NoMatches`, raised on a
    worker and reported as the whole application failing. CI hit it on a loaded
    runner; this laptop never did.

    Redrawing on every turn of the loop while the app starts is what makes the
    window reachable on demand. There is no flag to assert against instead:
    `is_mounted` goes true well before a widget's own children arrive, which is
    why the first fix for this did not take.
    """
    app = FlexiApp(db_path=seeded_db)
    raised: list[NoMatches] = []

    async def redraw_throughout_mounting() -> None:
        for _ in range(MOUNT_TICKS):
            try:
                app.refresh_open_screens()
            except NoMatches as error:
                raised.append(error)
                return
            await asyncio.sleep(0)

    hammer = asyncio.create_task(redraw_throughout_mounting())
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
    hammer.cancel()

    assert not raised, f"a redraw during mounting raised {raised[0]!r}"


async def test_a_stale_calendar_is_refetched_off_the_message_loop_and_redrawn(
    seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker writes on a session of its own, and asks the loop to redraw.

    It used to run SELECT, DELETE, INSERT and commit on the session the message
    loop owns, started in the statement after the one that pushes the dashboard
    — whose modules read that same session to draw. A `Session` is not
    thread-safe: the interleavings give `database is locked` or a reader
    finding the session inactive under a rolled-back write, and nothing catches
    either.

    Redrawing matters as much as fetching. Every figure on the dashboard
    depends on which days are holidays, so a calendar that lands after the
    first draw and is not shown leaves the balance wrong by a day per holiday
    until an unrelated keystroke.
    """
    stale = NOW - CACHE_MAX_AGE - timedelta(days=1)
    with get_session(create_db_engine(seeded_db)) as session:
        session.execute(update(BankHolidayRefresh).values(fetched_at=stale))
        session.commit()

    def answered(_self: object, url: str, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "england-and-wales": {
                    "division": "england-and-wales",
                    # Inside the week the dashboard opens on, so the arrival
                    # has to change what is already drawn.
                    "events": [{"title": "A new holiday", "date": "2026-06-12"}],
                }
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.Client, "get", answered)

    app = FlexiApp(db_path=seeded_db)
    async with app.run_test(size=WIDE) as pilot:
        # Pumped first, so `on_mount` has started the worker: `wait_for_complete`
        # returns at once if there is nothing to wait for. Then pumped until the
        # redraw lands rather than a fixed number of times, because the redraw
        # the worker asks for through `call_from_thread` is a callback the loop
        # schedules -- and a fixed count is a test measuring the weather.
        await pilot.pause()
        await app.workers.wait_for_complete()
        landed = date(2026, 6, 12)
        for _ in range(20):
            await pilot.pause()
            if app.services.ledger.day(landed).is_holiday:
                break

        assert app.services.bank_holidays.get_dates() == {landed}
        assert app.services.ledger.day(landed).is_holiday, (
            "the ledger was built before the calendar landed and never rebuilt"
        )
        assert not [
            line for line in await said(app, pilot) if "bank holiday" in line.lower()
        ], "a calendar that arrived is not a calendar that is missing"


async def test_a_calendar_that_lands_during_setup_has_no_dashboard_to_redraw(
    unconfigured: Path,
) -> None:
    """The worker can finish before the questions are answered.

    Nothing is wrong with that — there is simply no dashboard yet, and asking
    for one must not be an `AttributeError` on a screen the user is typing
    into.
    """
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SetupScreen)

        app.holidays_refreshed()
        await pilot.pause()

        showing(app, SetupScreen)


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
        announced = [
            line for line in await said(app, pilot) if "Update available" in line
        ]
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
        assert not [
            line for line in await said(app, pilot) if "Update available" in line
        ]


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

        assert "Insights is not built yet." in await said(app, pilot)
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

        assert sessions_on(app._session, TODAY) == []
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
        stored = build_services(app._session).settings.get_settings()
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
    """The service graph is wired before the answers exist, and still answers.

    It used to be rewired instead, because the bank-holiday division was read
    once at construction: a dashboard handed the graph built at launch would ask
    GOV.UK for the English calendar for the rest of a Scottish user's working
    life. Rewiring fixed that and introduced a worse one -- see
    `test_saving_settings_leaves_every_screen_on_the_same_registry`. The
    division is a question now, so neither is needed.
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
        assert app.services.bank_holidays.division is Division.SCOTLAND


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


async def test_saving_settings_leaves_every_screen_on_the_same_registry(
    app_factory: AppFactory,
) -> None:
    """One session, one registry, for the life of the application.

    Saving settings used to rebuild it, because `BankHolidayService` captured
    the division. Every screen already mounted kept the registry it was
    constructed with, while the modules inside those screens resolved the new
    one through the app -- so one dashboard drew itself from two graphs, and the
    half of it that went through the screen was still reading the old division.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        board = showing(app, DashboardScreen)
        before = app.services

        await pilot.press("f4")
        await pilot.pause()
        showing(app, SettingsScreen).query_one(
            "#select-division", Select
        ).value = "scotland"
        await pilot.click("#btn-save")
        await pilot.pause()

        showing(app, DashboardScreen)
        assert app.services is before, "the registry was replaced under the screens"
        assert board._services is app.services
        assert app.services.bank_holidays.division is Division.SCOTLAND


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


async def test_the_nav_highlight_follows_the_screen(app_factory: AppFactory) -> None:
    """Which tab is lit is a fact about the screen you are on.

    `App` carried a `_sync_nav` that walked `self.query(AppHeader)` and
    `self.query(NavBar)` after every navigation. `App.query` does not search the
    screen stack, so both loops ran zero times and the method had never once
    changed anything — which nothing noticed, because each screen's own header
    sets itself on mount and each `NavBar` reads `app.nav` when it mounts.

    That is the behaviour, so this is where it is pinned.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert [bar.active for bar in app.screen.query(NavBar)] == ["dashboard"]

        await pilot.press("f3")
        await pilot.pause()
        await pilot.pause()
        assert app.nav == "insights"
        assert [bar.active for bar in app.screen.query(NavBar)] == ["insights"]

        await pilot.press("f1")
        await pilot.pause()
        await pilot.pause()
        assert app.nav == "dashboard"
        assert [bar.active for bar in app.screen.query(NavBar)] == ["dashboard"]


async def test_moving_between_destinations_leaves_none_of_them_behind(
    app_factory: AppFactory,
) -> None:
    """One destination is open at a time, so opening one closes the last.

    `f3` then `f2` pushed Leave on top of Insights and overwrote the only
    reference to it. The stack grew, and `f1` — which dismisses whatever it is
    holding — took Leave off and revealed *Insights*, while the nav bar said
    Dashboard. Pressing it again did nothing, because by then the app believed
    it was already home.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        showing(app, InsightsScreen)

        await pilot.press("f2")
        await pilot.pause()
        showing(app, LeaveScreen)
        assert sum(isinstance(s, InsightsScreen) for s in app.screen_stack) == 0, (
            "the screen it left is not still underneath"
        )

        await pilot.press("f1")
        await pilot.pause()
        await pilot.pause()
        showing(app, DashboardScreen)
        assert app.nav == "dashboard"


async def test_the_stack_does_not_grow_however_long_somebody_browses(
    app_factory: AppFactory,
) -> None:
    """Twelve keystrokes should leave the stack the depth one does."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")
        await pilot.pause()
        depth = len(app.screen_stack)

        for key in ("f2", "f3", "f2", "f3", "f2"):
            await pilot.press(key)
            await pilot.pause()
            assert len(app.screen_stack) == depth, f"after {key}"

        await pilot.press("f1")
        await pilot.pause()
        await pilot.pause()
        showing(app, DashboardScreen)


async def test_escape_from_a_destination_still_comes_home(
    app_factory: AppFactory,
) -> None:
    """The swap must not break the ordinary way out.

    `_back` now ignores a dismissal of a screen that has already been replaced,
    and this is the case where it must not ignore it.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        showing(app, LeaveScreen)

        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()

        showing(app, DashboardScreen)
        assert app.nav == "dashboard"
