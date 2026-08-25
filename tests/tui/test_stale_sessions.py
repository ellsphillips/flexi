"""A session left open overnight, seen from the application.

The CLI sweeps stale sessions when it opens the database, in
`__main__._open_database`. The application never did. So a Tuesday somebody
forgot to close was still drawn as running when they opened Flexi on Thursday,
and pressing `/` closed Tuesday's session at Thursday's time -- one work session,
dated Tuesday, fifty-one hours long, and about forty-three hours of overtime
that never happened.

`flexi clock out` did the right thing with the identical database, because the
CLI had swept on the way in. Which of the two you reached for decided what got
written, and that is the tell: the invariant belonged to opening the database,
and only one of the two ways in established it.

Sweeping inside `clock_out` instead looks tempting and is wrong -- it would
auto-close backdated sessions before they could be closed properly, which is how
the demo seed and a good deal of the suite write history.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import time_machine

from flexi.app import FlexiApp
from flexi.constants import ClockAction
from flexi.models.database.db import Base, ClockEvent, WorkSession
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.moment import moment_of
from flexi.screens.dashboard import DashboardScreen
from flexi.services.registry import Services
from tests.conftest import sessions_on
from tests.tui.conftest import WIDE, showing

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
    Base.metadata.create_all(engine)
    session = get_session(engine)

    Services.build(session).settings.save_settings(
        leave_year_start="04-06",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
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


async def test_opening_the_application_closes_monday_at_its_own_evening(
    left_open: Path,
) -> None:
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(TUESDAY_TEN, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            showing(app, DashboardScreen)

            monday = sessions_on(app.services.session, MONDAY)
            assert len(monday) == 1
            assert monday[0].clock_out_event is not None, "still running on Tuesday"
            assert monday[0].auto_closed is True


async def test_pressing_the_clock_key_starts_tuesday_rather_than_ending_monday(
    left_open: Path,
) -> None:
    """The wrong figure: Monday 09:00 to Tuesday 10:00, recorded as one day."""
    app = FlexiApp(db_path=left_open)
    with time_machine.travel(TUESDAY_TEN, tick=False):
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            await pilot.press("slash")
            await pilot.pause()

            monday = sessions_on(app.services.session, MONDAY)
            assert len(monday) == 1
            assert monday[0].clock_out_event is not None
            worked = moment_of(monday[0].clock_out_event) - moment_of(
                monday[0].clock_in_event
            )
            assert worked < timedelta(hours=24), f"Monday was recorded as {worked}"
            assert monday[0].auto_closed is True, "closed by the sweep, not by the key"

            tuesday = sessions_on(app.services.session, TUESDAY_TEN.date())
            assert len(tuesday) == 1, "the key should have started a new day"
