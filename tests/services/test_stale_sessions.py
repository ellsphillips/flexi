"""Tests for Slice 6: stale-session auto-close.

Covers: stale close once, system audit event, count toward worked time,
23:59 fallback, and auto_closed flag.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from flexi.constants import ClockAction
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.services.clock import ClockService
from flexi.services.settings import SettingsService
from flexi.services.startup import close_stale_sessions


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_db_engine(tmp_path / "test.db")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    s = get_session(engine)
    yield s
    s.close()


@pytest.fixture()
def svc(session) -> ClockService:
    return ClockService(session)


@pytest.fixture()
def _settings(session):
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
        yesterday = datetime.now(tz=timezone.utc) - timedelta(days=1)
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
        yesterday = datetime.now(tz=timezone.utc) - timedelta(days=1)
        svc.clock_in(now=yesterday)
        closed = close_stale_sessions(svc._session, time(18, 0))
        assert closed[0].clock_out_event is not None
        assert closed[0].clock_out_event.source == "system"
        assert closed[0].clock_out_event.action is ClockAction.OUT

    def test_auto_closed_flag_set(self, svc: ClockService) -> None:
        yesterday = datetime.now(tz=timezone.utc) - timedelta(days=1)
        svc.clock_in(now=yesterday)
        closed = close_stale_sessions(svc._session, time(18, 0))
        assert closed[0].auto_closed is True

    def test_closes_only_once(self, svc: ClockService) -> None:
        yesterday = datetime.now(tz=timezone.utc) - timedelta(days=1)
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
            date.today() - timedelta(days=1),
            time(20, 0),
            tzinfo=timezone.utc,
        )
        svc.clock_in(now=yesterday_8pm)
        closed = close_stale_sessions(svc._session, time(18, 0))
        assert len(closed) == 1
        close_time = closed[0].clock_out_event.timestamp.replace(tzinfo=None).time()
        assert close_time == time(23, 59)


class TestCountsTowardWorkedTime:
    def test_auto_closed_session_has_duration(self, svc: ClockService) -> None:
        yesterday_9am = datetime.combine(
            date.today() - timedelta(days=1),
            time(9, 0),
            tzinfo=timezone.utc,
        )
        svc.clock_in(now=yesterday_9am)
        closed = close_stale_sessions(svc._session, time(18, 0))
        ws = closed[0]
        start = ws.clock_in_event.timestamp.replace(tzinfo=None)
        end = ws.clock_out_event.timestamp.replace(tzinfo=None)
        duration = (end - start).total_seconds()
        assert duration > 0  # Has positive duration
        assert duration == 9 * 3600  # 9am to 18:00 = 9 hours
