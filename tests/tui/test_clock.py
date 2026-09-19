"""Feature 1: clocking in and out is one key, and it is recorded."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import time_machine
from textual.widgets import Button, Input, Switch

from flexi.app import FlexiApp
from flexi.components.modules.clock import ClockModule
from flexi.constants import ClockAction
from flexi.messages import Scope
from flexi.models.database.db import Base, ClockEvent, WorkSession
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.moment import moment_of
from flexi.services.registry import build_services
from flexi.services.settings import parse_settings
from tests.conftest import sessions_on
from tests.tui.conftest import WIDE, AppFactory, dashboard, status_text


async def test_slash_clocks_out_and_back_in(app_factory: AppFactory) -> None:
    """It toggles, from the dashboard, with one unshifted key.

    There is no way to clock in twice from here — the key is a toggle — which is
    why the test that claimed to check that refusal at this layer has gone. It
    booted the application and then called the service directly, asserting a
    branch `tests/services/test_clock.py` already asserts in milliseconds.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        assert app.services.clock.is_clocked_in()  # the seed leaves a session open

        await pilot.press("slash")
        await pilot.pause()
        assert not app.services.clock.is_clocked_in()
        assert "Clocked out" in status_text(app)

        await pilot.press("slash")
        await pilot.pause()
        assert app.services.clock.is_clocked_in()
        assert "Clocked in" in status_text(app)


@pytest.mark.parametrize("destination", ["insights", "leave"])
async def test_the_receipt_lands_on_the_screen_in_front_of_you(
    app_factory: AppFactory, destination: str
) -> None:
    """`/` is bound on the application and works from every destination.

    The dashboard does the clocking and confirms it on its own footer, which is
    underneath Leave and Insights. Without this the key toggles the clock and
    the only visible footer says nothing, so the only way to tell whether it
    worked is to go back to the dashboard.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        app.action_go_to(destination)
        await pilot.pause()
        assert app.screen is not dashboard(app)

        await pilot.press("slash")
        await pilot.pause()

        assert not app.services.clock.is_clocked_in()
        assert "Clocked out" in status_text(app)
        assert app.nav == destination, "the receipt does not move anybody"


async def test_the_button_does_the_same_thing(app_factory: AppFactory) -> None:
    """It works for a pointer, because the point of Textual is that one works."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        button = app.screen.query_one("#clock-button", Button)
        assert str(button.label) == "Depart"

        await pilot.click("#clock-button")
        await pilot.pause()
        assert not app.services.clock.is_clocked_in()
        assert str(app.screen.query_one("#clock-button", Button).label) == "Arrive"


async def test_the_switch_reflects_the_truth_without_looping(
    app_factory: AppFactory,
) -> None:
    """It writes the switch back on every redraw without treating that as input.

    A naive handler acts on the write, clocks straight back out, redraws, and
    does it again — a loop that ends in a toast on every tick.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        switch = app.screen.query_one("#clock-switch", Switch)
        assert switch.value is True

        await pilot.press("slash")
        await pilot.pause()
        assert app.screen.query_one("#clock-switch", Switch).value is False
        assert not app.services.clock.is_clocked_in()

        # A redraw writes the switch back from the database. If that write were
        # treated as a user action the clock would flip again here.
        dashboard(app).refresh_modules(Scope.ALL)
        await pilot.pause()
        assert not app.services.clock.is_clocked_in()


async def test_slash_does_not_reach_a_focused_input(app_factory: AppFactory) -> None:
    """It gives the key to the field, so a typed date can contain a slash."""
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("g")  # go-to-date modal
        await pilot.pause()
        field = app.screen.query_one("#goto-input", Input)
        field.focus()
        await pilot.pause()

        before = app.services.clock.is_clocked_in()
        await pilot.press("slash")
        await pilot.pause()

        assert app.services.clock.is_clocked_in() is before
        assert "/" in field.value


async def test_the_switch_moves_through_its_watcher_so_it_animates(
    app_factory: AppFactory,
) -> None:
    """It sets the reactive rather than writing past it.

    `set_reactive` puts the value in without running the watcher, and the
    watcher is what slides the slider — the clock moved and the switch sat
    still. Asserting on the animation itself would be asserting on a tween, so
    this asserts on the thing that drives it: the reactive changed, and the
    widget's own watcher saw it.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        switch = app.screen.query_one("#clock-switch", Switch)
        seen: list[bool] = []
        original = switch.watch_value

        def record(value: bool) -> None:
            seen.append(value)
            original(value)

        switch.watch_value = record  # type: ignore[method-assign]

        await pilot.press("slash")
        await pilot.pause()

        assert seen == [False], (
            "the watcher should have run exactly once, with the new value"
        )


async def test_the_elapsed_time_is_in_the_border_subtitle(
    app_factory: AppFactory,
) -> None:
    """It puts the live figure in the module's data slot, not in a whole row.

    The date rides beside it, numeric and padded so the slot keeps one width all
    month. Off the clock there is no elapsed time and the date stands alone.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        clock = app.screen.query_one(ClockModule)
        assert str(clock.border_subtitle).endswith(" · 11/06/2026")
        assert ":" in str(clock.border_subtitle).split(" · ")[0]

        await pilot.press("slash")  # clock out
        await pilot.pause()
        assert str(app.screen.query_one(ClockModule).border_subtitle) == "11/06/2026"


async def test_a_write_through_the_screen_carries_the_live_tick_with_it(
    app_factory: AppFactory,
) -> None:
    """The screen owns the write, and the redraw, and the timer.

    A module never redraws its neighbours: it asks the screen, which reports
    the result, invalidates the ledger once, rebuilds whoever declared an
    interest, and starts or stops the one-second tick. Without that last part a
    session closed from a panel leaves a timer redrawing a clock that stopped.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = dashboard(app)
        assert screen._tick is not None, "the seed's open session should be ticking"

        screen.toggle_clock()
        await pilot.pause()

        assert str(app.screen.query_one("#clock-button", Button).label) == "Arrive"
        assert screen._tick is None, "a closed session left the timer running"


async def test_a_tick_keeps_every_day_but_today(app_factory: AppFactory) -> None:
    """The second that passes is only news about today.

    `_on_tick` cleared the whole ledger memo to refresh the live readout, and
    `LedgerService.days` already rebuilds today unconditionally for exactly
    that reason — an open session's length changes every second, so caching it
    would freeze the clock. Clearing the memo threw away every other day in the
    period too, so a month view re-derived thirty-one day ledgers a second to
    refresh the one the memo was never keeping.
    """
    app = app_factory()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        screen = dashboard(app)
        yesterday = screen.period.start
        kept = app.services.ledger.day(yesterday)

        screen._on_tick()
        await pilot.pause()

        assert app.services.ledger.day(yesterday) is kept, (
            "a day nobody wrote to was rebuilt because a second passed"
        )


# -- the day turning under an open session ---------------------------------

MONDAY = date(2026, 6, 8)
MONDAY_FIVE = datetime(2026, 6, 8, 17, 0, tzinfo=UTC)
TUESDAY_TEN = datetime(2026, 6, 9, 10, 0, tzinfo=UTC)


@pytest.fixture
def monday_open(tmp_path: Path) -> Path:
    """A configured database with Monday still on the clock."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    Base.metadata.create_all(engine)
    session = get_session(engine)

    build_services(session).settings.save_settings(
        parse_settings(
            leave_year_start="04-06",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    event = ClockEvent(
        action=ClockAction.IN,
        timestamp=datetime(2026, 6, 8, 9, 0),
        source="user",
    )
    session.add(event)
    session.flush()
    session.add(WorkSession(clock_in_id=event.id, work_date=MONDAY))
    session.commit()
    session.close()
    engine.dispose()
    return path


async def test_the_key_after_midnight_starts_today_rather_than_ending_yesterday(
    monday_open: Path,
) -> None:
    """The key acts on the fact the panel is showing.

    Left running overnight, the panel says "off the clock" the moment the date
    turns, because it reads today's ledger. The key read any open session, so
    the morning's `/` closed Monday at Tuesday's time: nineteen hours of work
    nobody did, on a session that cannot be edited or deleted. The application
    sweeps on mount, so this only showed up on a Flexi left open overnight.
    """
    app = FlexiApp(db_path=monday_open)
    with time_machine.travel(MONDAY_FIVE, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            assert app.services.clock.is_clocked_in()

            with time_machine.travel(TUESDAY_TEN, tick=False):
                await pilot.press("slash")
                await pilot.pause()

                monday = sessions_on(app._session, MONDAY)
                assert len(monday) == 1
                assert monday[0].auto_closed is True, "closed by the sweep, not the key"
                closed = monday[0].clock_out_event
                assert closed is not None, "the sweep closes what it sweeps"
                worked = moment_of(closed) - moment_of(monday[0].clock_in_event)
                assert worked < timedelta(hours=24), f"Monday was recorded as {worked}"

                tuesday = sessions_on(app._session, TUESDAY_TEN.date())
                assert len(tuesday) == 1, "the key should have started a new day"
