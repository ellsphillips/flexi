"""Tests for Slice 4: clock service.

Covers: accepted actions persist both ClockEvent and WorkSession,
rejected actions write nothing, DB rollback leaves no partial state.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import ClockAction
from flexi.models.database.db import ClockEvent, WorkSession
from flexi.services.clock import ClockService


@pytest.fixture
def svc(session: Session) -> ClockService:
    return ClockService(session)


# ---------- accepted actions persist ----------


class TestClockIn:
    def test_creates_event_and_session(
        self, svc: ClockService, session: Session
    ) -> None:
        result = svc.clock_in()
        assert result.success is True
        assert result.event is not None
        assert result.event.action is ClockAction.IN
        assert result.session is not None
        assert result.session.clock_out_id is None

        # Verify persisted
        events = session.execute(select(ClockEvent)).scalars().all()
        sessions = session.execute(select(WorkSession)).scalars().all()
        assert len(events) == 1
        assert len(sessions) == 1

    def test_sets_work_date(self, svc: ClockService) -> None:
        result = svc.clock_in()
        assert result.session is not None
        assert result.session.work_date == wallclock.today()


class TestClockOut:
    def test_creates_event_and_closes_session(
        self, svc: ClockService, session: Session
    ) -> None:
        svc.clock_in()
        result = svc.clock_out()
        assert result.success is True
        assert result.event is not None
        assert result.event.action is ClockAction.OUT
        assert result.session is not None
        assert result.session.clock_out_id is not None

        events = session.execute(select(ClockEvent)).scalars().all()
        assert len(events) == 2


# ---------- rejected actions write nothing ----------


class TestRejections:
    def test_duplicate_clock_in(self, svc: ClockService, session: Session) -> None:
        svc.clock_in()
        result = svc.clock_in()
        assert result.success is False
        assert result.event is None
        # Only one event from the first clock-in
        events = session.execute(select(ClockEvent)).scalars().all()
        assert len(events) == 1

    def test_clock_out_without_open_session(
        self, svc: ClockService, session: Session
    ) -> None:
        result = svc.clock_out()
        assert result.success is False
        assert result.event is None
        events = session.execute(select(ClockEvent)).scalars().all()
        assert len(events) == 0

    def test_clock_in_after_clock_out(self, svc: ClockService) -> None:
        svc.clock_in()
        svc.clock_out()
        result = svc.clock_in()
        assert result.success is True


# ---------- rollback leaves no partial state ----------


class TestRollback:
    def test_flush_failure_leaves_no_event(
        self, svc: ClockService, session: Session
    ) -> None:
        """If commit fails after flush, no partial state should remain."""
        with patch.object(session, "commit", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                svc.clock_in()

        session.rollback()
        events = session.execute(select(ClockEvent)).scalars().all()
        sessions = session.execute(select(WorkSession)).scalars().all()
        assert len(events) == 0
        assert len(sessions) == 0


# ---------- open session queries ----------


class TestOpenSession:
    def test_not_clocked_in_initially(self, svc: ClockService) -> None:
        assert svc.is_clocked_in() is False

    def test_clocked_in_after_clock_in(self, svc: ClockService) -> None:
        svc.clock_in()
        assert svc.is_clocked_in() is True

    def test_not_clocked_in_after_clock_out(self, svc: ClockService) -> None:
        svc.clock_in()
        svc.clock_out()
        assert svc.is_clocked_in() is False


class TestSessionsForDate:
    def test_returns_sessions(self, svc: ClockService) -> None:
        # A real session, with time in it. Clocking in and straight back out is
        # a slip of the finger and is discarded — see test_short_sessions.py.
        now = datetime.now(tz=UTC)
        svc.clock_in(now=now)
        svc.clock_out(now=now + timedelta(minutes=30))
        sessions = svc.get_sessions_for_date(wallclock.today())
        assert len(sessions) == 1

    def test_empty_for_other_date(self, svc: ClockService) -> None:
        svc.clock_in()
        assert svc.get_sessions_for_date(date(2020, 1, 1)) == []
