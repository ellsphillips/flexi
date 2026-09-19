"""A timesheet records the time on the wall, not an instant in UTC.

SQLite has no timestamp type, so a stored zone is dropped on the way back out
and the naive value is read as local. A 09:44 BST clock-in in London, stored as
UTC, would read back as "since 08:44" and count an hour it had not been.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from flexi.models.database.db import ClockEvent
from flexi.services.registry import build_services

NINE_FORTY_FOUR = datetime(2026, 6, 11, 9, 44)


def _stored(session: Session) -> list[datetime]:
    return [row.timestamp for row in session.query(ClockEvent).all()]


def test_time_stored_is_the_time_on_the_wall(session: Session) -> None:
    build_services(session).clock.clock_in(now=NINE_FORTY_FOUR)
    assert _stored(session) == [NINE_FORTY_FOUR]


def test_stored_time_reads_back_unchanged(session: Session) -> None:
    service = build_services(session).clock
    service.clock_in(now=NINE_FORTY_FOUR)
    open_session = service.get_open_session()
    assert open_session is not None
    assert open_session.clock_in_event.timestamp == NINE_FORTY_FOUR


@pytest.mark.usefixtures("in_london")
def test_aware_moment_is_converted_not_stripped(session: Session) -> None:
    """08:44+00:00 is 09:44 on BST, and stripping the zone loses that hour.

    A caller may still hand in an aware value, and older rows hold them. The
    London fixture is what gives the conversion an hour to find: under the
    suite's UTC pin there is none.
    """
    aware = datetime(2026, 6, 11, 8, 44, tzinfo=UTC)
    build_services(session).clock.clock_in(now=aware)

    stored = _stored(session)[0]
    assert stored.tzinfo is None
    assert stored == datetime(2026, 6, 11, 9, 44)


def test_session_lasts_what_the_clock_says(session: Session) -> None:
    service = build_services(session).clock
    service.clock_in(now=NINE_FORTY_FOUR)
    service.clock_out(now=NINE_FORTY_FOUR + timedelta(hours=7, minutes=24))

    events = sorted(_stored(session))
    assert events[1] - events[0] == timedelta(hours=7, minutes=24)


def test_work_date_is_the_local_day(session: Session) -> None:
    """A late start belongs to the local day it began on."""
    late = datetime(2026, 6, 11, 23, 30)
    build_services(session).clock.clock_in(now=late)
    open_session = build_services(session).clock.get_open_session()
    assert open_session is not None
    assert open_session.work_date == late.date()
