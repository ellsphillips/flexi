"""Voiding a session: the way back from a forgotten clock-out or a mistyped one.

A void takes the session out of every figure and keeps its clock events, which
are immutable and are the audit trail. The real hours then go back on as a
correction.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta

import pytest
import time_machine
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from flexi.models.database.db import ClockEvent, WorkSession
from flexi.models.database.engine import get_session
from flexi.services.registry import Services
from tests.services.conftest import Configured

MONDAY = date(2026, 9, 28)
TUESDAY = date(2026, 9, 29)
WEDNESDAY = date(2026, 9, 30)
TODAY = date(2026, 10, 1)
NOW = datetime.combine(TODAY, time(10, 0), tzinfo=UTC)

DAY = timedelta(hours=7, minutes=24)


@pytest.fixture(autouse=True)
def _on_the_day() -> Iterator[None]:
    """Hold the clock on the Thursday, so the sweep closes the days before it."""
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture
def services(configure: Configured) -> Services:
    """Tracking from Monday, so the balance is this week's arithmetic alone."""
    return configure(leave_year_start="01-01", tracking_since=MONDAY)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def punch(services: Services, day: date, opened: time, closed: time) -> int:
    """Clock a session in and out, and return its id."""
    assert services.clock.clock_in(now=at(day, opened.hour, opened.minute)).success
    result = services.clock.clock_out(now=at(day, closed.hour, closed.minute))
    assert result.session is not None, result.message
    return result.session.id


def left_running(services: Services, opened: datetime) -> int:
    """Clock in and never out; the sweep closes it at the auto-close time."""
    running = services.clock.clock_in(now=opened).session
    assert running is not None
    services.clock.sweep()
    return running.id


def events(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ClockEvent)) or 0


# What it does ----------------------------------------------------------------


def test_voiding_a_punched_session_takes_it_out_of_the_day(
    services: Services, session: Session
) -> None:
    voided = punch(services, MONDAY, time(9), time(17))

    result = services.clock.void(voided)

    assert result.success, result.message
    assert result.message == "Voided 09:00 → 17:00 on Mon 28 Sep"
    assert services.ledger.day(MONDAY).segments == ()
    row = session.get(WorkSession, voided)
    assert row is not None
    assert row.voided is True


def test_the_clock_events_are_kept(services: Services, session: Session) -> None:
    """The events are immutable, and they are the audit trail."""
    voided = punch(services, MONDAY, time(9), time(17))
    before = events(session)

    services.clock.void(voided)

    assert events(session) == before
    row = session.get(WorkSession, voided)
    assert row is not None
    assert row.clock_out_event is not None


def test_an_auto_closed_session_can_be_voided(services: Services) -> None:
    """The commonest reason to void: a clock-out forgotten and swept to 18:00."""
    swept = left_running(services, at(WEDNESDAY, 9))

    result = services.clock.void(swept)

    assert result.success, result.message
    assert result.message == "Voided 09:00 → 18:00 on Wed 30 Sep"
    assert services.ledger.day(WEDNESDAY).worked == timedelta()


def test_a_correction_can_be_voided(services: Services) -> None:
    """A mistyped correction is otherwise there for good: `n` refuses an overlap."""
    mistyped = services.clock.correct(TUESDAY, time(9), time(19)).session
    assert mistyped is not None

    result = services.clock.void(mistyped.id)

    assert result.success, result.message
    assert services.clock.correct(TUESDAY, time(9), time(17)).success
    assert services.ledger.day(TUESDAY).worked == timedelta(hours=8)


def test_the_ledger_reflects_it_at_once(services: Services) -> None:
    """A day memoised before the void is not served after it."""
    voided = punch(services, MONDAY, time(9), time(17))
    assert services.ledger.day(MONDAY).worked == timedelta(hours=8)

    services.clock.void(voided)

    assert services.ledger.day(MONDAY).worked == timedelta()
    assert services.ledger.balance(MONDAY).delta == -DAY


# What it refuses -------------------------------------------------------------


def test_a_running_session_is_refused(services: Services) -> None:
    """Its end is not known yet; clocking out is what gives it one."""
    running = services.clock.clock_in(now=at(TODAY, 9)).session
    assert running is not None

    result = services.clock.void(running.id)

    assert result.success is False
    assert result.message == "That session is still running; clock out first"
    assert services.clock.is_clocked_in()


def test_a_voided_session_is_refused(services: Services) -> None:
    voided = punch(services, MONDAY, time(9), time(17))
    assert services.clock.void(voided).success

    result = services.clock.void(voided)

    assert result.success is False
    assert result.message == "That session was already voided"


def test_an_unknown_session_is_refused(services: Services) -> None:
    result = services.clock.void(9999)

    assert result.success is False
    assert result.message == "No such session"


def test_it_holds_the_writer_while_it_decides(
    services: Services,
    session: Session,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One write transaction: no other writer lands between the read and the write."""
    voided = punch(services, MONDAY, time(9), time(17))
    get = session.get
    writer_was_blocked = False

    with get_session(engine) as competing:
        competing.connection().exec_driver_sql("PRAGMA busy_timeout=0")

        def interleave(model: type[WorkSession], ident: int) -> WorkSession | None:
            nonlocal writer_was_blocked
            row = get(model, ident)
            other = competing.get(WorkSession, ident)
            assert other is not None
            try:
                other.voided = True
                competing.commit()
            except OperationalError:
                competing.rollback()
                writer_was_blocked = True
            return row

        monkeypatch.setattr(session, "get", interleave)
        result = services.clock.void(voided)

    assert writer_was_blocked
    assert result.success, result.message


# The way back ----------------------------------------------------------------


def test_void_then_correct_gives_the_true_balance(services: Services) -> None:
    """Left at 16:00 without clocking out, and the sweep credits until 18:00.

    Monday and Tuesday are a contracted day each, so the balance is
    Wednesday's alone: 13:20 to 16:00 against 7:24.
    """
    punch(services, MONDAY, time(9), time(16, 24))
    punch(services, TUESDAY, time(9), time(16, 24))
    swept = left_running(services, at(WEDNESDAY, 13, 20))
    credited = timedelta(hours=4, minutes=40) - DAY
    assert services.ledger.balance(WEDNESDAY).delta == credited

    assert services.clock.void(swept).success
    assert services.clock.correct(WEDNESDAY, time(13, 20), time(16)).success

    worked = timedelta(hours=2, minutes=40) - DAY
    assert services.ledger.balance(WEDNESDAY).delta == worked
