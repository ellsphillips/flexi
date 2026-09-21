"""A session under the threshold is discarded, not recorded as work."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flexi.constants import ClockAction
from flexi.models.database.db import ClockEvent, WorkSession
from flexi.services.clock import ClockService
from flexi.services.registry import Services, build_services, invalidate_services
from tests.conftest import sessions_on
from tests.services.conftest import Configured

DAY = date(2026, 8, 10)
NINE = datetime.combine(DAY, datetime.min.time(), tzinfo=UTC).replace(hour=9)
NOON = NINE.replace(hour=12)


@pytest.fixture(autouse=True)
def _on_the_day() -> Iterator[None]:
    """Hold the clock at DAY, so every test here means the same thing every day.

    DAY is fixed and the stale sweep reads the clock, so an open session on it
    has to stay today's to escape the sweep.
    """
    with time_machine.travel(NOON, tick=False):
        yield


@pytest.fixture
def services(configure: Configured) -> Services:
    return configure()


def rows(session: Session) -> list[WorkSession]:
    return list(session.query(WorkSession).all())


def test_double_press_is_discarded(services: Services, session: Session) -> None:
    services.clock.clock_in(now=NINE)
    result = services.clock.clock_out(now=NINE + timedelta(seconds=2))

    assert result.success
    assert "Discarded" in result.message
    assert sessions_on(session, DAY) == []


def test_events_are_kept(services: Services, session: Session) -> None:
    """Voided, not deleted: clock events are immutable."""
    services.clock.clock_in(now=NINE)
    services.clock.clock_out(now=NINE + timedelta(seconds=2))

    assert len(rows(session)) == 1
    assert rows(session)[0].voided is True
    assert session.query(WorkSession).count() == 1


def test_discarded_session_is_not_counted(services: Services) -> None:
    services.clock.clock_in(now=NINE)
    services.clock.clock_out(now=NINE + timedelta(seconds=2))
    invalidate_services(services)

    ledger = services.ledger.day(DAY)
    assert ledger.segments == ()
    assert ledger.worked == timedelta()


def test_real_session_is_untouched(services: Services, session: Session) -> None:
    services.clock.clock_in(now=NINE)
    result = services.clock.clock_out(now=NINE + timedelta(minutes=3))

    assert result.message == "Clocked out"
    assert len(sessions_on(session, DAY)) == 1


def test_boundary_counts(services: Services) -> None:
    """Exactly the threshold is long enough."""
    services.clock.clock_in(now=NINE)
    result = services.clock.clock_out(now=NINE + timedelta(seconds=60))
    assert result.message == "Clocked out"


def test_threshold_is_configurable(session: Session) -> None:
    """Sixty seconds is the default, not a fixed rule."""
    built = build_services(session)
    clock = ClockService(
        session, built.settings, built.bank_holidays, timedelta(seconds=5)
    )
    clock.clock_in(now=NINE)
    assert "Discarded" in clock.clock_out(now=NINE + timedelta(seconds=3)).message

    clock.clock_in(now=NINE + timedelta(hours=1))
    assert (
        clock.clock_out(now=NINE + timedelta(hours=1, seconds=9)).message
        == "Clocked out"
    )


def test_discard_message_names_the_minute(services: Services) -> None:
    services.clock.clock_in(now=NINE)
    result = services.clock.clock_out(now=NINE + timedelta(seconds=1))
    assert result.message == "Discarded — under 1 minute on the clock"


# ---- sessions written before the threshold ----


def add_session(session: Session, start: datetime, end: datetime) -> None:
    """Write a session straight to the table.

    Clocking in through ClockService runs the startup sweep, which would void
    each row as the next one was created.
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


def test_old_short_sessions_are_not_reinterpreted_on_startup(
    services: Services, session: Session
) -> None:
    """A changed preference cannot void work that was already accepted."""
    for offset in range(5):
        at = NINE + timedelta(minutes=offset)
        add_session(session, at, at + timedelta(seconds=1))
    add_session(session, NINE + timedelta(hours=2), NINE + timedelta(hours=4))

    assert len(sessions_on(session, DAY)) == 6

    built = build_services(session)
    built.clock.sweep()
    assert len(sessions_on(session, DAY)) == 6
    assert not any(work.voided for work in rows(session))


def test_open_session_is_never_swept(services: Services, session: Session) -> None:
    """An open session has no length yet, so it cannot be too short."""
    services.clock.clock_in(now=NINE)
    built = build_services(session)
    built.clock.sweep()
    assert services.clock.is_clocked_in()


def test_schema_refuses_a_dangling_clock_out(
    services: Services, session: Session
) -> None:
    """A closed session always resolves the event that defines its duration."""
    services.clock.clock_in(now=NINE)
    services.clock.clock_out(now=NINE + timedelta(hours=8))
    work = session.execute(select(WorkSession)).scalar_one()

    work.clock_out_id = 9999  # an event that is not there
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
