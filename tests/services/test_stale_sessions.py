"""A session left open overnight is closed at the configured time, not now."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import ClockAction
from flexi.services.clock import ClockService
from flexi.services.startup import close_stale_sessions
from tests.services.conftest import Configured


@pytest.fixture
def svc(configure: Configured) -> ClockService:
    """A configured application, which this file only appeared to have.

    There was a `_settings` fixture here that nothing requested and no
    `usefixtures` mark, so all eight tests ran against `SettingsService`
    defaults while reading as though they were configured. The sweep behaved
    the same either way, which is why it went unnoticed — but a test that looks
    configured and is not will mislead the next person to change the defaults.
    """
    return configure(leave_year_start="01-01", entitlement=(2026, 25.0)).clock


class TestStaleSessionClose:
    def test_closes_previous_day(self, svc: ClockService, session: Session) -> None:
        yesterday = datetime.now(tz=UTC) - timedelta(days=1)
        svc.clock_in(now=yesterday)
        closed = close_stale_sessions(session, time(18, 0))
        assert len(closed) == 1
        assert closed[0].clock_out_id is not None

    def test_does_not_close_today(self, svc: ClockService, session: Session) -> None:
        svc.clock_in()
        closed = close_stale_sessions(session, time(18, 0))
        assert closed == []
        assert svc.is_clocked_in() is True

    def test_system_audit_event(self, svc: ClockService, session: Session) -> None:
        yesterday = datetime.now(tz=UTC) - timedelta(days=1)
        svc.clock_in(now=yesterday)
        closed = close_stale_sessions(session, time(18, 0))
        assert closed[0].clock_out_event is not None
        assert closed[0].clock_out_event.source == "system"
        assert closed[0].clock_out_event.action is ClockAction.OUT

    def test_auto_closed_flag_set(self, svc: ClockService, session: Session) -> None:
        yesterday = datetime.now(tz=UTC) - timedelta(days=1)
        svc.clock_in(now=yesterday)
        closed = close_stale_sessions(session, time(18, 0))
        assert closed[0].auto_closed is True

    def test_closes_only_once(self, svc: ClockService, session: Session) -> None:
        yesterday = datetime.now(tz=UTC) - timedelta(days=1)
        svc.clock_in(now=yesterday)
        close_stale_sessions(session, time(18, 0))
        second = close_stale_sessions(session, time(18, 0))
        assert second == []

    def test_noop_when_no_stale(self, svc: ClockService, session: Session) -> None:
        assert close_stale_sessions(session, time(18, 0)) == []


class TestFallbackTo2359:
    def test_close_before_clock_in_uses_2359(
        self, svc: ClockService, session: Session
    ) -> None:
        # Clock in at 20:00 yesterday, auto-close configured at 18:00
        yesterday_8pm = datetime.combine(
            wallclock.today() - timedelta(days=1),
            time(20, 0),
            tzinfo=UTC,
        )
        svc.clock_in(now=yesterday_8pm)
        closed = close_stale_sessions(session, time(18, 0))
        assert len(closed) == 1
        closing = closed[0].clock_out_event
        assert closing is not None
        close_time = closing.timestamp.replace(tzinfo=None).time()
        assert close_time == time(23, 59)

    def test_clock_in_at_auto_close_closes_at_that_time(
        self, svc: ClockService, session: Session
    ) -> None:
        yesterday_6pm = datetime.combine(
            wallclock.today() - timedelta(days=1),
            time(18, 0),
            tzinfo=UTC,
        )
        svc.clock_in(now=yesterday_6pm)

        closed = close_stale_sessions(session, time(18, 0))

        closing = closed[0].clock_out_event
        assert closing is not None
        assert closing.timestamp.time() == time(18, 0)


class TestCountsTowardWorkedTime:
    def test_auto_closed_session_has_duration(
        self, svc: ClockService, session: Session
    ) -> None:
        yesterday_9am = datetime.combine(
            wallclock.today() - timedelta(days=1),
            time(9, 0),
            tzinfo=UTC,
        )
        svc.clock_in(now=yesterday_9am)
        closed = close_stale_sessions(session, time(18, 0))
        ws = closed[0]
        assert ws.clock_out_event is not None
        start = ws.clock_in_event.timestamp.replace(tzinfo=None)
        end = ws.clock_out_event.timestamp.replace(tzinfo=None)
        duration = (end - start).total_seconds()
        assert duration > 0  # Has positive duration
        assert duration == 9 * 3600  # 9am to 18:00 = 9 hours


def test_the_sweep_can_be_told_what_day_it_is(
    svc: ClockService, session: Session
) -> None:
    """`today` is a parameter so a caller can sweep as at a date it chooses.

    `run_startup_cleanup` lets it default to the wall clock, but the auto-close
    backfill in `flexi init` sweeps a database it has just migrated as at the
    day it is doing the migrating — and a test that only ever lets it read the
    clock cannot tell the two apart.
    """
    monday = date(2026, 8, 10)
    svc.clock_in(now=datetime.combine(monday, time(9, 0), tzinfo=UTC))

    assert close_stale_sessions(session, time(18, 0), today=monday) == [], (
        "as at the day itself, the session is not stale"
    )

    closed = close_stale_sessions(
        session, time(18, 0), today=monday + timedelta(days=1)
    )

    assert len(closed) == 1
    assert closed[0].auto_closed is True
