"""A session left open overnight is closed at the configured time, not now."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import ClockAction
from flexi.services.clock import ClockService
from flexi.services.registry import Services
from flexi.services.settings import SettingsService
from flexi.services.startup import close_stale_sessions


@pytest.fixture
def svc(session: Session) -> ClockService:
    return Services.build(session).clock


@pytest.fixture
def _settings(session: Session) -> None:
    s = SettingsService(session)
    s.save_settings(
        leave_year_start="01-01",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    s.save_entitlement(2026, 25.0)


class TestStaleSessionClose:
    def test_closes_previous_day(self, svc: ClockService) -> None:
        yesterday = datetime.now(tz=UTC) - timedelta(days=1)
        svc.clock_in(now=yesterday)
        closed = close_stale_sessions(svc._session, time(18, 0))
        assert len(closed) == 1
        assert closed[0].clock_out_id is not None

    def test_does_not_close_today(self, svc: ClockService) -> None:
        svc.clock_in()
        closed = close_stale_sessions(svc._session, time(18, 0))
        assert closed == []
        assert svc.is_clocked_in() is True

    def test_system_audit_event(self, svc: ClockService) -> None:
        yesterday = datetime.now(tz=UTC) - timedelta(days=1)
        svc.clock_in(now=yesterday)
        closed = close_stale_sessions(svc._session, time(18, 0))
        assert closed[0].clock_out_event is not None
        assert closed[0].clock_out_event.source == "system"
        assert closed[0].clock_out_event.action is ClockAction.OUT

    def test_auto_closed_flag_set(self, svc: ClockService) -> None:
        yesterday = datetime.now(tz=UTC) - timedelta(days=1)
        svc.clock_in(now=yesterday)
        closed = close_stale_sessions(svc._session, time(18, 0))
        assert closed[0].auto_closed is True

    def test_closes_only_once(self, svc: ClockService) -> None:
        yesterday = datetime.now(tz=UTC) - timedelta(days=1)
        svc.clock_in(now=yesterday)
        close_stale_sessions(svc._session, time(18, 0))
        second = close_stale_sessions(svc._session, time(18, 0))
        assert second == []

    def test_noop_when_no_stale(self, svc: ClockService) -> None:
        assert close_stale_sessions(svc._session, time(18, 0)) == []


class TestFallbackTo2359:
    def test_close_before_clock_in_uses_2359(self, svc: ClockService) -> None:
        # Clock in at 20:00 yesterday, auto-close configured at 18:00
        yesterday_8pm = datetime.combine(
            wallclock.today() - timedelta(days=1),
            time(20, 0),
            tzinfo=UTC,
        )
        svc.clock_in(now=yesterday_8pm)
        closed = close_stale_sessions(svc._session, time(18, 0))
        assert len(closed) == 1
        closing = closed[0].clock_out_event
        assert closing is not None
        close_time = closing.timestamp.replace(tzinfo=None).time()
        assert close_time == time(23, 59)


class TestCountsTowardWorkedTime:
    def test_auto_closed_session_has_duration(self, svc: ClockService) -> None:
        yesterday_9am = datetime.combine(
            wallclock.today() - timedelta(days=1),
            time(9, 0),
            tzinfo=UTC,
        )
        svc.clock_in(now=yesterday_9am)
        closed = close_stale_sessions(svc._session, time(18, 0))
        ws = closed[0]
        assert ws.clock_out_event is not None
        start = ws.clock_in_event.timestamp.replace(tzinfo=None)
        end = ws.clock_out_event.timestamp.replace(tzinfo=None)
        duration = (end - start).total_seconds()
        assert duration > 0  # Has positive duration
        assert duration == 9 * 3600  # 9am to 18:00 = 9 hours
