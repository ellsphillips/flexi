"""A slip of the finger is not a minute of work."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from flexi.models.database.app import create_db_engine, get_session
from flexi.constants import ClockAction
from flexi.models.database.db import Base, ClockEvent, WorkSession
from flexi.services.clock import ClockService
from flexi.services.registry import Services
from flexi.services.startup import run_startup_cleanup

DAY = date(2026, 8, 10)
NINE = datetime.combine(DAY, datetime.min.time(), tzinfo=timezone.utc).replace(hour=9)


@pytest.fixture()
def session(tmp_path: Path):
    engine = create_db_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    opened = get_session(engine)
    yield opened
    opened.close()


@pytest.fixture()
def services(session) -> Services:
    built = Services.build(session)
    built.settings.save_settings(
        leave_year_start="10-20",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    return Services.build(session)


def rows(session) -> list[WorkSession]:
    return list(session.query(WorkSession).all())


def test_a_double_press_is_discarded(services: Services, session) -> None:
    """Clocking in and straight back out never happened."""
    services.clock.clock_in(now=NINE)
    result = services.clock.clock_out(now=NINE + timedelta(seconds=2))

    assert result.success
    assert "Discarded" in result.message
    assert services.clock.get_sessions_for_date(DAY) == []


def test_the_events_are_kept(services: Services, session) -> None:
    """Voided, not deleted. Clock events are immutable and the trail is the point."""
    services.clock.clock_in(now=NINE)
    services.clock.clock_out(now=NINE + timedelta(seconds=2))

    assert len(rows(session)) == 1
    assert rows(session)[0].voided is True
    assert session.query(WorkSession).count() == 1


def test_a_discarded_session_is_absent_from_the_arithmetic(services: Services) -> None:
    """It is not a short day, it is no day at all."""
    services.clock.clock_in(now=NINE)
    services.clock.clock_out(now=NINE + timedelta(seconds=2))
    services.invalidate()

    ledger = services.ledger.day(DAY)
    assert ledger.segments == ()
    assert ledger.worked == timedelta()


def test_a_real_session_is_untouched(services: Services) -> None:
    """The threshold has to be short enough that nobody loses an errand to it."""
    services.clock.clock_in(now=NINE)
    result = services.clock.clock_out(now=NINE + timedelta(minutes=3))

    assert result.message == "Clocked out"
    assert len(services.clock.get_sessions_for_date(DAY)) == 1


def test_the_boundary_counts(services: Services) -> None:
    """Exactly the threshold is long enough."""
    services.clock.clock_in(now=NINE)
    result = services.clock.clock_out(now=NINE + timedelta(seconds=60))
    assert result.message == "Clocked out"


def test_the_threshold_is_configurable(session) -> None:
    """Sixty seconds is a default, not a law."""
    clock = ClockService(session, timedelta(seconds=5))
    clock.clock_in(now=NINE)
    assert "Discarded" in clock.clock_out(now=NINE + timedelta(seconds=3)).message

    clock.clock_in(now=NINE + timedelta(hours=1))
    assert clock.clock_out(now=NINE + timedelta(hours=1, seconds=9)).message == "Clocked out"


def test_the_message_reads_like_a_person_said_it(services: Services) -> None:
    services.clock.clock_in(now=NINE)
    result = services.clock.clock_out(now=NINE + timedelta(seconds=1))
    assert result.message == "Discarded — under 1 minute on the clock"


# -- databases that predate the threshold ----------------------------------


def add_session(session, start: datetime, end: datetime) -> None:
    """A session written straight to the table.

    Not through ClockService: clocking in runs the startup sweep, so a loop that
    used the service would void each row as it created the next and there would
    be nothing left for the sweep to find.
    """
    events = []
    for action, when in ((ClockAction.IN, start), (ClockAction.OUT, end)):
        event = ClockEvent(action=action, timestamp=when, source="user")
        session.add(event)
        session.flush()
        events.append(event)
    session.add(
        WorkSession(
            clock_in_id=events[0].id,
            clock_out_id=events[1].id,
            work_date=start.date(),
        )
    )
    session.commit()


def test_old_short_sessions_are_swept_on_startup(services: Services, session) -> None:
    """Somebody learning which key does what leaves a trail of them."""
    for offset in range(5):
        at = NINE + timedelta(minutes=offset)
        add_session(session, at, at + timedelta(seconds=1))
    add_session(session, NINE + timedelta(hours=2), NINE + timedelta(hours=4))

    assert len(services.clock.get_sessions_for_date(DAY)) == 6

    run_startup_cleanup(session)
    assert len(services.clock.get_sessions_for_date(DAY)) == 1
    assert len(rows(session)) == 6, "voided, not deleted"


def test_an_open_session_is_never_swept(services: Services, session) -> None:
    """It has no length yet, so it cannot be too short."""
    services.clock.clock_in(now=NINE)
    run_startup_cleanup(session)
    assert services.clock.is_clocked_in()
