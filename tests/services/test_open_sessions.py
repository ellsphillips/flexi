"""A session left open is worth its own day, not every hour since.

Startup auto-closes stale sessions, so this only matters in the window between
a crash and the next launch.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from flexi.constants import ClockAction
from flexi.models.database.db import ClockEvent, WorkSession
from flexi.services.registry import Services, invalidate_services

TUESDAY = date(2026, 8, 11)
THURSDAY = date(2026, 8, 13)
TUESDAY_NINE = datetime.combine(TUESDAY, datetime.min.time(), tzinfo=UTC).replace(
    hour=9
)
THURSDAY_NOON = datetime.combine(THURSDAY, datetime.min.time()).replace(hour=12)


def _leave_open(session: Session, at: datetime) -> None:
    """An open session written straight to the table, past the auto-close."""
    event = ClockEvent(action=ClockAction.IN, timestamp=at, source="user")
    session.add(event)
    session.flush()
    session.add(WorkSession(clock_in_id=event.id, work_date=at.date()))
    session.commit()


def test_open_past_day_stops_at_its_last_minute(
    services: Services, session: Session
) -> None:
    """09:00 to 23:59, in whole minutes, and not to the microsecond before midnight."""
    _leave_open(session, TUESDAY_NINE)
    invalidate_services(services)

    tuesday = services.ledger.day(TUESDAY, now=THURSDAY_NOON)

    assert tuesday.worked == timedelta(hours=14, minutes=59)


def test_open_day_does_not_count_days_since(
    services: Services, session: Session
) -> None:
    _leave_open(session, TUESDAY_NINE)
    invalidate_services(services)

    tuesday = services.ledger.day(TUESDAY, now=THURSDAY_NOON)

    assert tuesday.worked != THURSDAY_NOON - TUESDAY_NINE.replace(tzinfo=None)


def test_open_session_today_still_runs_live(
    services: Services, session: Session
) -> None:
    """Today is not clamped: the balance ticks up while it is watched."""
    _leave_open(session, TUESDAY_NINE)
    invalidate_services(services)

    watching = datetime.combine(TUESDAY, datetime.min.time()).replace(
        hour=11, minute=30
    )
    tuesday = services.ledger.day(TUESDAY, now=watching)

    assert tuesday.worked == timedelta(hours=2, minutes=30)


def test_open_session_runs_to_the_minute_on_the_clock(
    services: Services, session: Session
) -> None:
    """In at 09:00:30 and watched at 12:00:45, the day has the 3:00 it shows."""
    _leave_open(session, TUESDAY_NINE.replace(second=30))
    invalidate_services(services)

    watching = TUESDAY_NINE.replace(hour=12, second=45)
    assert services.ledger.day(TUESDAY, now=watching).worked == timedelta(hours=3)


def test_a_cached_open_session_reaches_its_cutoff_after_midnight(
    services: Services, session: Session
) -> None:
    _leave_open(session, TUESDAY_NINE)
    watching = TUESDAY_NINE.replace(hour=23)
    before_midnight = services.ledger.day(TUESDAY, now=watching)
    assert before_midnight.worked == timedelta(hours=14)

    after_midnight = services.ledger.day(TUESDAY, now=watching + timedelta(hours=2))

    assert after_midnight.worked == timedelta(hours=14, minutes=59)
    assert after_midnight is not before_midnight
