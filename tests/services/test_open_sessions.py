"""A session nobody closed is worth its own day, not every hour since.

Startup auto-closes stale sessions, so this only matters in the window between
a crash and the next launch. During it, a Tuesday left open used to report every
hour from Tuesday morning to right now as time worked on Tuesday.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from flexi.constants import ClockAction
from flexi.models.database.db import ClockEvent, WorkSession
from flexi.services.registry import Services

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


def test_an_open_past_day_stops_at_its_own_midnight(
    services: Services, session: Session
) -> None:
    _leave_open(session, TUESDAY_NINE)
    services.invalidate()

    tuesday = services.ledger.day(TUESDAY, now=THURSDAY_NOON)

    assert tuesday.worked < timedelta(days=1)
    assert tuesday.worked == timedelta(
        hours=14, minutes=59, seconds=59, microseconds=999999
    )


def test_it_does_not_count_the_days_since(services: Services, session: Session) -> None:
    """The bug: two days and three hours of 'work' on a single Tuesday."""
    _leave_open(session, TUESDAY_NINE)
    services.invalidate()

    tuesday = services.ledger.day(TUESDAY, now=THURSDAY_NOON)

    assert tuesday.worked != THURSDAY_NOON - TUESDAY_NINE.replace(tzinfo=None)


def test_an_open_session_today_still_runs_live(
    services: Services, session: Session
) -> None:
    """Today is not clamped -- the balance has to tick up while it is watched."""
    _leave_open(session, TUESDAY_NINE)
    services.invalidate()

    watching = datetime.combine(TUESDAY, datetime.min.time()).replace(
        hour=11, minute=30
    )
    tuesday = services.ledger.day(TUESDAY, now=watching)

    assert tuesday.worked == timedelta(hours=2, minutes=30)
