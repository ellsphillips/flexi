"""A timesheet records the time the person lived, not an instant in UTC.

Clock-in used to store datetime.now(tz=UTC). SQLite has no timestamp type, so
the zone was dropped on the way back out and the naive UTC value was read as
though it were local: somebody in London clocking in at 09:44 BST saw
"since 08:44", and every open session counted an hour it had not been.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from flexi.models.database.db import ClockEvent
from flexi.services.clock import ClockService

NINE_FORTY_FOUR = datetime(2026, 6, 11, 9, 44)


def _stored(session: Session) -> list[datetime]:
    return [row.timestamp for row in session.query(ClockEvent).all()]


def test_the_time_stored_is_the_time_on_the_wall(session: Session) -> None:
    ClockService(session).clock_in(now=NINE_FORTY_FOUR)
    assert _stored(session) == [NINE_FORTY_FOUR]


def test_it_reads_back_as_it_went_in(session: Session) -> None:
    """The round trip is what the old code lost."""
    service = ClockService(session)
    service.clock_in(now=NINE_FORTY_FOUR)
    open_session = service.get_open_session()
    assert open_session is not None
    assert open_session.clock_in_event.timestamp == NINE_FORTY_FOUR


def test_an_aware_moment_is_converted_rather_than_stripped(session: Session) -> None:
    """08:44+00:00 is 09:44 to somebody on BST, and stripping loses them an hour.

    A caller may still hand in an aware value, and older rows hold them.
    """
    aware = datetime(2026, 6, 11, 8, 44, tzinfo=UTC)
    ClockService(session).clock_in(now=aware)

    stored = _stored(session)[0]
    assert stored.tzinfo is None
    assert stored == aware.astimezone().replace(tzinfo=None)


def test_a_session_lasts_what_the_clock_says(session: Session) -> None:
    service = ClockService(session)
    service.clock_in(now=NINE_FORTY_FOUR)
    service.clock_out(now=NINE_FORTY_FOUR + timedelta(hours=7, minutes=24))

    events = sorted(_stored(session))
    assert events[1] - events[0] == timedelta(hours=7, minutes=24)


def test_the_work_date_is_the_local_day(session: Session) -> None:
    """A late finish belongs to the day it started, in the wearer's own calendar."""
    late = datetime(2026, 6, 11, 23, 30)
    ClockService(session).clock_in(now=late)
    open_session = ClockService(session).get_open_session()
    assert open_session is not None
    assert open_session.work_date == late.date()
