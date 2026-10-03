"""A session left open overnight, seen from the application.

Sweeping stale sessions belongs to opening the database, so both ways in (the
CLI in `__main__.open_database` and the application) have to do it. Sweeping
inside `clock_out` instead would auto-close backdated sessions before they
could be closed properly, which is how the demo seed writes history.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import time_machine

from flexi.app import FlexiApp
from flexi.components.chrome import AppHeader
from flexi.components.modules.records import RecordsModule
from flexi.constants import ClockAction
from flexi.models.database.db import ClockEvent, WorkSession
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.moment import moment_of
from flexi.screens.dashboard import DashboardScreen
from flexi.services.registry import build_services
from flexi.services.settings import parse_settings
from tests.conftest import session_at, sessions_on
from tests.database import create_schema
from tests.tui.conftest import WIDE, showing, status_text

MONDAY = date(2026, 6, 8)
MONDAY_NINE = datetime.combine(MONDAY, datetime.min.time(), tzinfo=UTC).replace(hour=9)
TUESDAY_TEN = datetime.combine(
    MONDAY + timedelta(days=1), datetime.min.time(), tzinfo=UTC
).replace(hour=10)


@pytest.fixture
def left_open(tmp_path: Path) -> Path:
    """A configured database with Monday still on the clock."""
    path = tmp_path / "flexi.db"
    engine = create_db_engine(path)
    create_schema(engine)
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
        action=ClockAction.IN, timestamp=MONDAY_NINE.replace(tzinfo=None), source="user"
    )
    session.add(event)
    session.flush()
    session.add(WorkSession(clock_in_id=event.id, work_date=MONDAY))
    session.commit()
    session.close()
    engine.dispose()
    return path


async def test_opening_closes_monday_at_its_own_evening(
    left_open: Path,
) -> None:
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(TUESDAY_TEN, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            showing(app, DashboardScreen)

            monday = sessions_on(app._session, MONDAY)
            assert len(monday) == 1
            assert monday[0].clock_out_event is not None, "still running on Tuesday"
            assert monday[0].auto_closed is True


async def test_opening_says_what_it_closed(left_open: Path) -> None:
    """The auto-close time can be hours after Monday ended, so it is said."""
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(TUESDAY_TEN, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()

            assert (
                "Mon 8 Jun was left running and closed at 18:00 (9:00 counted). "
                "If you left earlier: open the day in Records, press x on the "
                "session, then n."
            ) in [notice.message for notice in app._notifications]


async def test_nothing_left_running_goes_unannounced(left_open: Path) -> None:
    """Still Monday: opening has nothing to close, and `/` just clocks out."""
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(MONDAY_NINE + timedelta(hours=8), tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()

            assert not app.services.clock.is_clocked_in()
            assert not [
                notice.message
                for notice in app._notifications
                if "left running" in notice.message
            ]


async def test_clock_key_starts_tuesday_not_ends_monday(
    left_open: Path,
) -> None:
    """Without the sweep this records Monday 09:00 to Tuesday 10:00 as one day."""
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(TUESDAY_TEN, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()

            monday = sessions_on(app._session, MONDAY)
            assert len(monday) == 1
            assert monday[0].clock_out_event is not None
            worked = moment_of(monday[0].clock_out_event) - moment_of(
                monday[0].clock_in_event
            )
            assert worked < timedelta(hours=24), f"Monday was recorded as {worked}"
            assert monday[0].auto_closed is True, "closed by the sweep, not by the key"

            tuesday = sessions_on(app._session, TUESDAY_TEN.date())
            assert len(tuesday) == 1, "the key should have started a new day"


# ---- the date turning under an open dashboard ----

MONDAY_FIVE = MONDAY_NINE.replace(hour=17)
JUST_AFTER_MIDNIGHT = TUESDAY_TEN.replace(hour=0, second=30)


async def test_open_dashboard_closes_monday_when_the_date_turns(
    left_open: Path,
) -> None:
    """Without a key pressed, Monday counts to midnight and the tick runs on.

    Midnight is six hours past the auto-close the sweep will record, so the
    balance drops by them on the morning's first `/`.
    """
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(MONDAY_FIVE, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            board = showing(app, DashboardScreen)
            assert board._tick is not None, "Monday is still on the clock"

            with time_machine.travel(JUST_AFTER_MIDNIGHT, tick=False):
                board._on_tick()
                await pilot.pause()

                monday = sessions_on(app._session, MONDAY)
                assert monday[0].auto_closed is True, "closed by the sweep"
                closed = monday[0].clock_out_event
                assert closed is not None
                assert moment_of(closed) == MONDAY_NINE.replace(hour=18)
                assert board.period.anchor == JUST_AFTER_MIDNIGHT.date()
                row = board.query_one(RecordsModule).table.get_row(f"d-{MONDAY}")
                assert str(row[2]) == "9:00", "Monday counted past its auto-close"
                assert board._tick is None, "nothing is open, so nothing ticks"


async def test_the_date_turning_says_what_it_closed(left_open: Path) -> None:
    """Nobody pressed anything, so the notice says Monday was cut.

    The notice is gone in seconds, so the status bar says so as well.
    """
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(MONDAY_FIVE, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            board = showing(app, DashboardScreen)

            with time_machine.travel(JUST_AFTER_MIDNIGHT, tick=False):
                board._on_tick()
                await pilot.pause()

            assert (
                "Mon 8 Jun was left running and closed at 18:00 (9:00 counted). "
                "If you left earlier: open the day in Records, press x on the "
                "session, then n."
            ) in [notice.message for notice in app._notifications]
            assert status_text(app) == "Closed the session left running"


HALF_PAST_MIDNIGHT = JUST_AFTER_MIDNIGHT.replace(minute=30, second=0)


async def test_the_first_key_after_midnight_does_not_clock_in(
    left_open: Path,
) -> None:
    """The tick closed Monday unseen, so this `/` is as likely a clock-out.

    It finds nothing left to sweep, and stops where a press that swept would:
    clocking in would open a session nobody is working.
    """
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(MONDAY_FIVE, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            board = showing(app, DashboardScreen)

            with time_machine.travel(JUST_AFTER_MIDNIGHT, tick=False):
                board._on_tick()
                await pilot.pause()

            with time_machine.travel(HALF_PAST_MIDNIGHT, tick=False):
                await pilot.press("slash")
                await pilot.pause()

                assert not app.services.clock.is_clocked_in()
                assert status_text(app) == (
                    "Closed the session left running; press again to clock in"
                )

                await pilot.press("slash")
                await pilot.pause()

                assert app.services.clock.is_clocked_in(), "the next press does"


async def test_a_session_opened_since_midnight_is_closed_by_the_key(
    left_open: Path,
) -> None:
    """Only a press that finds nothing open is spent on what midnight closed."""
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(MONDAY_FIVE, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            board = showing(app, DashboardScreen)

            with time_machine.travel(JUST_AFTER_MIDNIGHT, tick=False):
                board._on_tick()
                await pilot.pause()
                # As `flexi clock in` from a shell would, and the poll draws.
                with session_at(left_open) as shell:
                    assert build_services(shell).clock.clock_in().success
                app.notice_other_writers()
                await pilot.pause()

            with time_machine.travel(HALF_PAST_MIDNIGHT, tick=False):
                await pilot.press("slash")
                await pilot.pause()

                assert not app.services.clock.is_clocked_in()
                assert status_text(app) == "Clocked out at 00:30"


async def test_a_day_view_of_monday_moves_on_to_tuesday(left_open: Path) -> None:
    """The header names the day the dashboard moved to, not the one it left."""
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(MONDAY_FIVE, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            board = showing(app, DashboardScreen)
            board.action_zoom("day")

            with time_machine.travel(JUST_AFTER_MIDNIGHT, tick=False):
                board._on_tick()
                await pilot.pause()

                assert board.period.anchor == JUST_AFTER_MIDNIGHT.date()
                assert board.query_one(AppHeader).context == board.period.label


async def test_a_period_moved_off_today_stays_where_it_was(
    left_open: Path,
) -> None:
    """Only a period that showed the old date follows the new one."""
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(MONDAY_FIVE, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            board = showing(app, DashboardScreen)
            board.action_shift(-1)
            browsed = board.period

            with time_machine.travel(JUST_AFTER_MIDNIGHT, tick=False):
                board._on_tick()
                await pilot.pause()

                assert board.period == browsed
                assert sessions_on(app._session, MONDAY)[0].auto_closed is True
