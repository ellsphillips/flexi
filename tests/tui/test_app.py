"""The application shell: opening, navigation, and what it says on the way in."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from threading import get_ident

import httpx
import pytest
from sqlalchemy import delete, update
from textual.pilot import Pilot
from textual.widgets import Input, Select

import flexi
from flexi.app import FlexiApp
from flexi.components.chrome import NavBar, VersionTag
from flexi.components.modules.records import RecordsModule
from flexi.constants import Division
from flexi.context import flexi_app
from flexi.models.database.db import BankHolidayCache, BankHolidayRefresh, Base
from flexi.models.database.engine import create_db_engine
from flexi.screens.dashboard import DashboardScreen
from flexi.screens.insights import InsightsScreen
from flexi.screens.leave import LeaveScreen
from flexi.screens.settings import SettingsScreen
from flexi.screens.setup import SetupScreen
from flexi.services.bank_holidays import CACHE_MAX_AGE, BankHolidayService
from flexi.services.samples import NOW
from flexi.versioning import UPGRADE_HINT
from tests.conftest import session_at
from tests.tui.conftest import (
    READABLE,
    WIDE,
    AppFactory,
    contrast,
    dashboard,
    showing,
)

TODAY = date(2026, 6, 11)
"""The Thursday the frozen clock is standing on."""


async def said(app: FlexiApp, pilot: Pilot[None]) -> list[str]:
    """Every notification the application has raised, oldest first.

    Both notices on this screen come from `@work(thread=True)`, and a thread is
    not a message, so `pilot.pause()` has nothing of theirs to drain. The
    workers are waited on first.
    """
    await app.workers.wait_for_complete()
    await pilot.pause()
    return [notification.message for notification in app._notifications]


@pytest.fixture
def unconfigured(tmp_path: Path) -> Path:
    """A migrated database with the setup questions unanswered."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    Base.metadata.create_all(engine)
    engine.dispose()
    return path


# Opening --------------------------------------------------------------------


async def test_open_settings_flag_opens_settings_over_dashboard(
    app_factory: AppFactory,
) -> None:
    """`flexi init` has no screen to push onto, so it sets a flag read at mount."""
    app = app_factory()
    app.open_settings = True
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SettingsScreen)
        assert app.dashboard() is not None, "settings should open over the dashboard"


async def test_declining_setup_closes_the_application(unconfigured: Path) -> None:
    """Nothing is pushed behind the setup screen, so a dismissal leaves no way out."""
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SetupScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert app.return_code == 0, "escape should close the application, cleanly"
        assert app.services.settings.get_settings() is None, "nothing was answered"


# On the way in --------------------------------------------------------------


MISSING_CALENDAR = "No bank holiday calendar. Days off will count as working days."


def empty_the_calendar(path: Path) -> None:
    """Leave the database with no cached holidays for any division."""
    with session_at(path) as session:
        session.execute(delete(BankHolidayCache))
        session.execute(delete(BankHolidayRefresh))
        session.commit()


async def test_empty_calendar_is_reported_on_launch(
    seeded_db: Path,
) -> None:
    empty_the_calendar(seeded_db)

    app = FlexiApp(db_path=seeded_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert MISSING_CALENDAR in await said(app, pilot)


async def test_stale_calendar_is_not_reported_missing(
    seeded_db: Path,
) -> None:
    """A stale calendar still answers every question for the year it holds."""
    stale = NOW - CACHE_MAX_AGE - timedelta(days=1)
    with session_at(seeded_db) as session:
        session.execute(update(BankHolidayRefresh).values(fetched_at=stale))
        session.commit()

    app = FlexiApp(db_path=seeded_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert app.services.bank_holidays.is_available(), "the calendar is still there"
        assert MISSING_CALENDAR not in await said(app, pilot)


async def test_failing_fetcher_leaves_the_app_running(
    seeded_db: Path,
) -> None:
    """`httpx.Client` raises `ImportError` under a SOCKS proxy with no `socksio`.

    The worker still has to post its completion, or the warning below is never
    said and an explicit refresh reports nothing.
    """
    empty_the_calendar(seeded_db)

    def unreachable() -> object:
        msg = "Using SOCKS proxy, but the 'socksio' package is not installed"
        raise ImportError(msg)

    app = FlexiApp(db_path=seeded_db, bank_holiday_fetcher=unreachable)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        announced = await said(app, pilot)

        assert app.is_running
        assert app.return_code is None
        assert MISSING_CALENDAR in announced


async def test_failing_update_check_leaves_the_app_running(
    app_factory: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception on a thread worker is fatal by default; this check is not."""

    def broken() -> str | None:
        msg = "the environment broke httpx"
        raise RuntimeError(msg)

    monkeypatch.setattr("flexi.app.available_update", broken)
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.is_running
        assert app.return_code is None
        showing(app, DashboardScreen)


async def test_fresh_calendar_is_left_alone(seeded_db: Path) -> None:
    """A fresh cache means no round trip and nothing to report."""
    with session_at(seeded_db) as session:
        session.execute(update(BankHolidayRefresh).values(fetched_at=NOW))
        session.commit()

    app = FlexiApp(db_path=seeded_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert not [
            line for line in await said(app, pilot) if "bank holiday" in line.lower()
        ]


MOUNT_TICKS = 20
"""Event loop turns to redraw across; the window opens four or five turns in."""


async def test_redraw_during_mount_is_not_a_crash(
    seeded_db: Path,
) -> None:
    """`refresh_open_screens` can reach a dashboard whose cells are not composed.

    It runs off the message loop when the bank holiday worker finishes, so it
    lands between two levels of the tree and raises `NoMatches` on a worker.
    `is_mounted` goes true before a widget's own children arrive, so there is no
    flag to wait on instead.
    """
    app = FlexiApp(db_path=seeded_db)
    ticks = 0

    async def redraw_throughout_mounting() -> None:
        nonlocal ticks
        for _ in range(MOUNT_TICKS):
            app.refresh_open_screens()
            ticks += 1
            await asyncio.sleep(0)

    hammer = asyncio.create_task(redraw_throughout_mounting())
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        # Awaiting inside the block re-raises whatever the task hit; an
        # unretrieved exception from a dead task is only a log line.
        await hammer

    assert ticks == MOUNT_TICKS


async def test_stale_calendar_refetches_off_loop_and_redraws(
    seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker fetches only; the message loop persists and redraws.

    A SQLAlchemy ``Session`` is not thread-safe, so the freshness read and the
    persistence belong to Textual's loop and the HTTP request alone runs on the
    worker. Every figure on the dashboard depends on which days are holidays, so
    a calendar that lands after the first draw has to be drawn.
    """
    stale = NOW - CACHE_MAX_AGE - timedelta(days=1)
    with session_at(seeded_db) as session:
        session.execute(update(BankHolidayRefresh).values(fetched_at=stale))
        session.commit()

    def answered(
        _self: object, request: httpx.Request, **_kwargs: object
    ) -> httpx.Response:
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
            request=request,
        )

    monkeypatch.setattr(httpx.Client, "send", answered)

    freshness_threads: list[int] = []
    persistence_threads: list[int] = []
    original_is_fresh = BankHolidayService.is_fresh
    original_cache_payload = BankHolidayService.cache_payload

    def record_freshness(service: BankHolidayService) -> bool:
        freshness_threads.append(get_ident())
        return original_is_fresh(service)

    def record_persistence(service: BankHolidayService, payload: object) -> bool:
        persistence_threads.append(get_ident())
        return original_cache_payload(service, payload)

    monkeypatch.setattr(BankHolidayService, "is_fresh", record_freshness)
    monkeypatch.setattr(BankHolidayService, "cache_payload", record_persistence)

    message_loop_thread = get_ident()
    app = FlexiApp(db_path=seeded_db)
    async with app.run_test(size=WIDE) as pilot:
        # Paused first so `on_mount` has started the worker: `wait_for_complete`
        # returns at once when there is nothing yet to wait for.
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
        assert freshness_threads == [message_loop_thread]
        assert persistence_threads == [message_loop_thread]


async def test_calendar_landing_during_setup_finds_no_dashboard(
    unconfigured: Path,
) -> None:
    """The worker can finish before the questions are answered."""
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SetupScreen)

        app.holidays_refreshed()
        await pilot.pause()

        showing(app, SetupScreen)


async def test_newer_release_is_announced_with_the_command(
    app_factory: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("flexi.app.available_update", lambda: "99.0.0")
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        announced = [
            line for line in await said(app, pilot) if "Update available" in line
        ]
        assert announced, "a newer version should be announced"
        assert "99.0.0" in announced[0]
        assert UPGRADE_HINT in announced[0]


async def test_being_up_to_date_says_nothing(app_factory: AppFactory) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert not [
            line for line in await said(app, pilot) if "Update available" in line
        ]


# Navigation -----------------------------------------------------------------


async def test_navigating_to_the_current_screen_does_nothing(
    app_factory: AppFactory,
) -> None:
    """Without the guard, F1 pushes a second copy of the screen already showing."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        depth = len(app.screen_stack)

        await pilot.press("f1")
        await pilot.pause()

        assert len(app.screen_stack) == depth
        showing(app, DashboardScreen)


async def test_insights_before_setup_says_finish_setup_first(
    unconfigured: Path,
) -> None:
    """Insights reads the dashboard's period, and before setup there is none."""
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("f3")
        await pilot.pause()

        assert "Finish setup first." in await said(app, pilot)
        showing(app, SetupScreen)


async def test_clock_key_does_nothing_during_setup(
    unconfigured: Path,
) -> None:
    """The clock binding is application-wide and `priority=True`, so it fires here.

    Clocking in on the setup screen would write against an unchosen pattern.
    """
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.set_focus(None)  # the slash reaches a focused field as a character

        await pilot.press("slash")
        await pilot.pause()

        assert not app.services.clock.is_clocked_in()
        showing(app, SetupScreen)


async def test_settings_cannot_open_over_setup(
    unconfigured: Path,
) -> None:
    """Saving the settings form is what marks an install configured.

    It has no entitlement field, so a form over the questions is a second way to
    finish setup, with no allowance at all.
    """
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("f4")
        await pilot.pause()

        showing(app, SetupScreen)
        assert "Finish setup first." in await said(app, pilot)
        assert not app.services.settings.is_setup_complete()


async def test_clock_key_works_from_any_screen(
    app_factory: AppFactory,
) -> None:
    """The binding is application-wide; the dashboard owns the record and redraw."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f3")  # insights, with the dashboard underneath
        await pilot.pause()
        assert app.services.clock.is_clocked_in()

        await pilot.press("slash")
        await pilot.pause()

        assert not app.services.clock.is_clocked_in()


async def test_dashboard_opens_with_the_answers_from_setup(
    unconfigured: Path,
) -> None:
    """The service graph is wired before the answers exist, and still answers.

    The bank-holiday division is read per query, so the graph built at launch
    serves the dashboard opened after setup without being rebuilt.
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


async def test_leave_opens_on_the_dashboards_anchor(
    app_factory: AppFactory,
) -> None:
    """F2 hands the leave screen the dashboard's anchor, not today."""
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


async def test_escaping_leave_relabels_the_nav_bar(
    app_factory: AppFactory,
) -> None:
    """A bar still reading "Leave" makes the next F2 a move to where you are."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        showing(app, LeaveScreen)

        await pilot.press("escape")
        await pilot.pause()

        showing(app, DashboardScreen)
        assert app.nav == "dashboard"


async def test_going_home_with_nothing_pushed_only_relabels(
    app_factory: AppFactory,
) -> None:
    """`nav` is what the bar draws, `_pushed` is what F1 pops; they can disagree."""
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


# Settings -------------------------------------------------------------------


ST_ANDREWS = date(2026, 11, 30)
"""The one Scottish holiday the stub calendar below carries."""


def scottish_calendar() -> object:
    """A GOV.UK index holding Scotland and nothing else."""
    return {
        "scotland": {
            "events": [{"title": "St Andrew\u2019s Day", "date": "2026-11-30"}]
        }
    }


async def test_calendar_is_fetched_for_the_chosen_division(
    unconfigured: Path,
) -> None:
    """Freshness is per division: a fetch before the question caches the wrong one."""
    app = FlexiApp(db_path=unconfigured, bank_holiday_fetcher=scottish_calendar)
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
        await app.workers.wait_for_complete()
        await pilot.pause()

        showing(app, DashboardScreen)
        assert app.services.bank_holidays.get_dates() == {ST_ANDREWS}


async def test_changing_the_division_refetches_the_calendar(
    seeded_db: Path,
) -> None:
    """The cache is keyed by division, so a new region starts with nothing."""
    app = FlexiApp(db_path=seeded_db, bank_holiday_fetcher=scottish_calendar)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("f4")
        await pilot.pause()
        showing(app, SettingsScreen).query_one(
            "#select-division", Select
        ).value = "scotland"
        await pilot.click("#btn-save")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.services.bank_holidays.division is Division.SCOTLAND
        assert app.services.bank_holidays.get_dates() == {ST_ANDREWS}


async def test_leaving_settings_without_saving_rebuilds_nothing(
    app_factory: AppFactory,
) -> None:
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


async def test_saving_settings_keeps_one_registry(
    app_factory: AppFactory,
) -> None:
    """One session, one registry, for the life of the application.

    A mounted screen keeps the registry it was constructed with while the
    modules inside it resolve theirs through the app, so replacing the registry
    draws one dashboard from two graphs.
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


async def test_saved_working_pattern_redraws_the_dashboard(
    app_factory: AppFactory,
) -> None:
    """Dropping a day changes what every figure on the dashboard is measured against."""
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


async def test_nav_highlight_follows_the_screen(app_factory: AppFactory) -> None:
    """Each screen's header and `NavBar` read `app.nav` when they mount.

    `App.query` does not search the screen stack, so nothing above them can set
    the highlight for them.
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


async def test_moving_between_destinations_leaves_none_behind(
    app_factory: AppFactory,
) -> None:
    """One destination is open at a time, so opening one closes the last.

    `f1` dismisses whatever the app is holding, so a second screen pushed over
    the first would be revealed by going home.
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


async def test_dashboard_key_closes_the_settings_form(
    app_factory: AppFactory,
) -> None:
    """Settings has no nav item, so `nav` still says dashboard while it is up.

    The guard that refuses a move to where you already are has to tell that case
    from an idle F1 on the dashboard.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f4")
        await pilot.pause()
        showing(app, SettingsScreen)

        await pilot.press("f1")
        await pilot.pause()
        await pilot.pause()

        showing(app, DashboardScreen)
        assert app._settings is None


async def test_leave_key_over_settings_reveals_leave(
    app_factory: AppFactory,
) -> None:
    """The screen underneath is holding the year it was scrolled to."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        opened = showing(app, LeaveScreen)

        await pilot.press("f4")
        await pilot.pause()
        showing(app, SettingsScreen)

        await pilot.press("f2")
        await pilot.pause()
        await pilot.pause()

        assert showing(app, LeaveScreen) is opened, "the leave screen was rebuilt"
        assert app._settings is None
        assert app.nav == "leave"


async def test_stack_does_not_grow_while_browsing(
    app_factory: AppFactory,
) -> None:
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
    """`_back` ignores the dismissal of a replaced screen; this is not one."""
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


def test_flexi_app_satisfies_the_composed_contract(
    seeded_db: Path,
) -> None:
    """Nothing in `src` calls `flexi_app`, so only this exercises both halves."""
    app = FlexiApp(db_path=seeded_db)

    assert flexi_app(app) is app


# The version tag ------------------------------------------------------------


def version_tag(app: FlexiApp) -> VersionTag:
    return app.screen.query_one(VersionTag)


async def test_header_names_the_installed_version(
    app_factory: AppFactory,
) -> None:
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        tag = version_tag(app)

        assert str(tag.render()) == f"v{flexi.__version__}"
        assert not tag.has_class("-outdated")
        assert tag.tooltip is None


async def test_version_is_readable_and_matches_the_wordmark(
    app_factory: AppFactory,
) -> None:
    """Contrast is measured on the rendered pixels, not named in the stylesheet."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        tag, mark = version_tag(app), app.screen.query_one(".wordmark-name")

        assert contrast(tag.colors[3].rgb, tag.background_colors[1].rgb) >= READABLE
        assert tag.colors[3].rgb == mark.colors[3].rgb, "the same grey as the wordmark"


async def test_newer_release_is_offered_in_the_header(
    app_factory: AppFactory,
) -> None:
    """The command to run does not fit beside a version number, so it is a tooltip."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.update_offered("99.0.0")
        await pilot.pause()
        tag = version_tag(app)

        assert str(tag.render()) == f"v{flexi.__version__} → v99.0.0"
        assert tag.has_class("-outdated")
        assert UPGRADE_HINT in str(tag.tooltip), "one sentence, said everywhere"


async def test_screen_opened_later_hears_about_the_release(
    app_factory: AppFactory,
) -> None:
    """A screen pushed after the check has a header that was not there to be told.

    The header is mounted without an application in other tests, so it cannot
    reach upward for one; the application hands the version over on the way in.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.update_offered("99.0.0")
        await pilot.pause()

        await pilot.press("f2")
        await pilot.pause()
        await pilot.pause()

        assert str(version_tag(app).render()) == f"v{flexi.__version__} → v99.0.0"


async def test_current_build_leaves_every_header_quiet(
    app_factory: AppFactory,
) -> None:
    """`dress_headers` runs on every push, and has nothing to say on most."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("f3")
        await pilot.pause()
        await pilot.pause()

        assert not version_tag(app).has_class("-outdated")


async def test_ignored_preferences_are_reported_on_launch(
    app_factory: AppFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`BINDINGS` reads the file before there is a screen to report a fallback on.

    A section that falls back is otherwise invisible: the file looks as though
    it is in force and the keys in use are the unchosen defaults.
    """
    monkeypatch.setattr("flexi.app.CONFIG_PROBLEM", "hotkeys could not be used")
    app = app_factory()

    async with app.run_test(size=WIDE) as pilot:
        assert "hotkeys could not be used" in await said(app, pilot)


async def test_valid_config_file_is_not_mentioned(
    app_factory: AppFactory,
) -> None:
    app = app_factory()

    async with app.run_test(size=WIDE) as pilot:
        assert not any("preferences" in note for note in await said(app, pilot))
