"""The command palette: the long tail of actions, including the keyless ones.

The palette is where an action goes when it does not earn a key. That makes it
the one surface where a command can be listed for months while pointing at a
callable that no longer exists — nothing type-checks a palette entry, and nobody
presses one often enough to notice. So these tests do not only ask what the
palette offers; they run the commands and assert that the application moved.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from threading import Lock, get_ident
from time import sleep

import pytest
from textual.command import DiscoveryHit, Hit
from textual.notifications import Notification
from textual.widgets import Input, RadioSet

from flexi.app import FlexiApp
from flexi.constants import AbsenceType, Granularity
from flexi.context import command_app
from flexi.models.database.db import Base
from flexi.models.database.engine import create_db_engine
from flexi.provider import Command, FlexiCommands, commands
from flexi.screens.modals import AbsenceModal
from flexi.screens.setup import SetupScreen
from tests.conftest import settled
from tests.tui.conftest import WIDE, AppFactory, dashboard, showing


async def discovered(app: FlexiApp) -> list[DiscoveryHit]:
    """Everything the palette offers on the screen that is showing.

    ``Hits`` is declared as either kind of hit, because one provider serves both
    the empty palette and a search. Discovery yields only the first kind, and
    narrowing here is what lets the tests below read ``hit.display``.
    """
    provider = FlexiCommands(app.screen)
    return [hit async for hit in provider.discover() if isinstance(hit, DiscoveryHit)]


async def titles(app: FlexiApp) -> list[str]:
    return [str(hit.display) for hit in await discovered(app)]


def notification(app: FlexiApp, message: str) -> Notification:
    """The notification carrying this message, asserted to be there.

    Not `list(app._notifications)[-1]`. The application starts a worker at mount
    that fills the bank holiday cache and says so when it cannot, so which
    notification is *last* depends on when that worker lands — which under
    coverage, or on a loaded machine, is sometimes after the command being
    tested. Asking for the one that matters is both stronger and stable.
    """
    for note in app._notifications:
        if note.message == message:
            return note
    said = [n.message for n in app._notifications]
    msg = f"no notification said {message!r}; got {said}"
    raise AssertionError(msg)


async def run_command(app: FlexiApp, title: str) -> None:
    """Choose the named entry, the way the palette does when it is clicked."""
    for hit in await discovered(app):
        if str(hit.display) == title:
            hit.command()
            return
    msg = f"the palette does not offer {title!r}"
    raise AssertionError(msg)


TODAY = date(2026, 6, 11)
"""The Thursday the frozen clock is standing on."""


@pytest.fixture
def unconfigured(tmp_path: Path) -> Path:
    """A migrated but unanswered database, which opens on setup."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    Base.metadata.create_all(engine)
    engine.dispose()
    return path


# -- the catalogue ---------------------------------------------------------


async def test_the_palette_offers_every_action_that_has_no_key(
    app_factory: AppFactory,
) -> None:
    """The keymap stays small because this list does not have to.

    Booking one specific absence type is the case that matters: there are five
    of them, each with a shifted key nobody memorises, and the palette is the
    route for the person who never learned them.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        offered = await titles(app)
        catalogue = commands(command_app(app))

        assert isinstance(catalogue, tuple)
        assert all(isinstance(command, Command) for command in catalogue)
        assert [command.title for command in catalogue] == offered
        assert "Clock in or out" in offered
        assert "Help" in offered
        assert "Go to Leave" in offered
        for granularity in Granularity:
            assert f"Period: {granularity.label.lower()}" in offered
        for kind in AbsenceType:
            assert f"Book {kind.phrase}…" in offered
        assert "Refresh bank holidays" in offered


async def test_every_entry_explains_itself(app_factory: AppFactory) -> None:
    """A list of forty verbs is only usable if each one says what it does."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        for hit in await discovered(app):
            assert hit.help, f"{hit.display} has no help text"


async def test_the_palette_offers_nothing_that_needs_a_screen_that_is_not_there(
    unconfigured: Path,
) -> None:
    """A command is only listed when the thing it acts on exists.

    Half the catalogue is bound to the dashboard — its period, its modals. On
    the setup screen there is no dashboard, and an entry that captured a missing
    one would fail with an AttributeError the moment somebody chose it, which is
    the least recoverable place for one to happen.
    """
    app = FlexiApp(db_path=unconfigured)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        showing(app, SetupScreen)
        offered = await titles(app)

        assert "Clock in or out" in offered
        assert "Go to Settings" in offered
        assert not [title for title in offered if title.startswith("Period:")]
        assert "Go to date…" not in offered
        assert "Refresh bank holidays" not in offered


# -- searching -------------------------------------------------------------


async def test_typing_narrows_the_list_and_ranks_what_is_left(
    app_factory: AppFactory,
) -> None:
    """The palette is a search box, so a query has to exclude as well as match."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        provider = FlexiCommands(app.screen)
        hits = [
            hit
            async for hit in provider.search("bank holidays")
            if isinstance(hit, Hit)
        ]

        assert [str(hit.match_display) for hit in hits] == ["Refresh bank holidays"]
        assert hits[0].score > 0


async def test_a_query_that_matches_nothing_yields_nothing(
    app_factory: AppFactory,
) -> None:
    """It offers no consolation prize, which would be chosen by accident."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        provider = FlexiCommands(app.screen)
        assert [hit async for hit in provider.search("xyzzy")] == []


# -- running what it offers ------------------------------------------------


async def test_choosing_a_period_entry_moves_the_dashboard(
    app_factory: AppFactory,
) -> None:
    """The four period entries are built in a loop, and each keeps its own span.

    A loop that closed over the variable instead of binding it gives four
    entries that all do whatever the last one said — the classic version of this
    bug, and invisible until somebody chooses the first entry and lands on a
    year.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(app, "Period: month")
        await pilot.pause()
        assert dashboard(app).period.granularity is Granularity.MONTH

        await run_command(app, "Period: day")
        await pilot.pause()
        assert dashboard(app).period.granularity is Granularity.DAY


async def test_choosing_go_to_today_comes_home(app_factory: AppFactory) -> None:
    """The entry for the person who has browsed into next March."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("right_square_bracket", "right_square_bracket")
        await pilot.pause()
        assert not dashboard(app).period.contains(TODAY)

        await run_command(app, "Go to today")
        await pilot.pause()
        assert dashboard(app).period.contains(TODAY)


async def test_choosing_go_to_date_opens_the_same_prompt_the_key_does(
    app_factory: AppFactory,
) -> None:
    """One action behind both routes, so they cannot drift apart."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(app, "Go to date…")
        await pilot.pause()
        assert app.screen.query("#goto-input")


async def test_choosing_an_absence_entry_opens_the_booking_it_named(
    app_factory: AppFactory,
) -> None:
    """It pre-fills the type, so the entry is the whole decision."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(app, f"Book {AbsenceType.SICK.phrase}…")
        await pilot.pause()

        modal = showing(app, AbsenceModal)
        pressed = modal.query_one("#absence-type", RadioSet).pressed_button
        assert pressed is not None, "the dialog opened with no type chosen"
        assert pressed.name == AbsenceType.SICK.value
        assert modal.query_one("#absence-date", Input).value == str(
            dashboard(app).period.anchor
        )


async def test_choosing_clock_in_or_out_clocks(app_factory: AppFactory) -> None:
    """The palette reaches the same toggle as the key, from the same screen."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert app.services.clock.is_clocked_in()

        await run_command(app, "Clock in or out")
        await pilot.pause()
        assert not app.services.clock.is_clocked_in()


# -- refreshing the calendar -----------------------------------------------


async def test_refreshing_the_calendar_reports_that_it_could_not_reach_govuk(
    app_factory: AppFactory,
) -> None:
    """The suite refuses the connection, which is what a train does too.

    A refresh that failed silently would leave somebody looking at a calendar
    with no bank holidays in it, believing they had just fetched one.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(app, "Refresh bank holidays")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert notification(app, "Could not reach gov.uk").severity == "warning"


async def test_a_successful_refresh_says_so_and_redraws_from_the_new_calendar(
    seeded_db: Path,
) -> None:
    """Invalidating matters as much as the message.

    Every figure on the dashboard is derived from which days are working days,
    so a fetch that filled the cache and left the caches above it alone would
    report success and change nothing on screen until the next keystroke.

    Asserted as "the ledger built from the old calendar is gone", not as "the
    cache is empty". Empty is true only in the instant between the refresh
    invalidating it and the redraw it triggers filling it again, so which of
    those the assertion arrived between was a property of how loaded the runner
    was -- green on a laptop, red on a busy one, with the same commit passing
    and failing in two runs an hour apart.

    Yesterday rather than today, because `days` rebuilds today on every call
    whether the cache was invalidated or not: it is the one day whose figures
    move while you are looking at them.
    """
    message_loop_thread = get_ident()
    fetch_threads: list[int] = []

    def fetch() -> object:
        fetch_threads.append(get_ident())
        return {
            "england-and-wales": {
                "events": [{"title": "A new holiday", "date": "2026-06-12"}]
            }
        }

    app = FlexiApp(db_path=seeded_db, bank_holiday_fetcher=fetch)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        # The dashboard measures itself after its first layout and rebuilds when
        # that lands. Deriving days again is exactly what this test is watching
        # for, so it has to be finished happening before the cache is read.
        await settled(pilot)
        ledger = app.services.ledger
        yesterday = TODAY - timedelta(days=1)
        derived = ledger.day(yesterday)
        assert ledger._cache[yesterday] is derived, "the day was not cached"

        await run_command(app, "Refresh bank holidays")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert notification(app, "Bank holidays refreshed").severity == "information"
        assert len(fetch_threads) == 1, "force refreshes even while the cache is fresh"
        assert fetch_threads[0] != message_loop_thread, "network I/O stays off the loop"
        assert ledger._cache.get(yesterday) is not derived, (
            "the day derived from the old calendar is still cached"
        )


async def test_repeated_refresh_requests_cannot_overlap_replace_transactions(
    seeded_db: Path,
) -> None:
    """Two quick palette choices may fetch twice, but never write together."""
    guard = Lock()
    active = 0
    most_active = 0
    requests = 0

    def fetch() -> object:
        nonlocal active, most_active, requests
        with guard:
            active += 1
            requests += 1
            most_active = max(most_active, active)
        sleep(0.05)
        with guard:
            active -= 1
        return {
            "england-and-wales": {
                "events": [{"title": "A new holiday", "date": "2026-06-12"}]
            }
        }

    app = FlexiApp(db_path=seeded_db, bank_holiday_fetcher=fetch)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.refresh_holidays(force=True)
        app.refresh_holidays(force=True)
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert requests == 2
    assert most_active == 1
