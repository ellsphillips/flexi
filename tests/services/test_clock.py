"""Clocking in and out writes both rows, or neither.

A ClockEvent without its WorkSession is a session that never ends; the reverse
is a session with no start. Every rejected action is checked for writing
nothing at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import ClockAction
from flexi.models.database.db import BankHolidayCache, ClockEvent, WorkSession
from flexi.services.clock import ClockService
from flexi.services.registry import Services

SCOTTISH_HOLIDAY = date(2027, 1, 4)
"""2 January, observed. Scotland only."""

ENGLISH_HOLIDAY = date(2027, 5, 3)
"""Early May. England & Wales only."""


@pytest.fixture
def svc(session: Session) -> ClockService:
    return Services.build(session).clock


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
        with (
            patch.object(session, "commit", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
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


class TestBankHolidayDivision:
    """The guard has to follow the configured division, not the default one."""

    @staticmethod
    def _configured(session: Session, division: str) -> Services:
        built = Services.build(session)
        built.settings.save_settings(
            leave_year_start="04-06",
            working_days="0,1,2,3,4,5,6",
            bank_holiday_division=division,
            auto_close_time="18:00",
        )
        stamped = datetime.now(UTC).replace(tzinfo=None)
        for when, owner in (
            (SCOTTISH_HOLIDAY, "scotland"),
            (ENGLISH_HOLIDAY, "england-and-wales"),
        ):
            session.add(
                BankHolidayCache(
                    date=when, title="test", division=owner, fetched_at=stamped
                )
            )
        session.commit()
        return Services.build(session)

    def test_a_scottish_user_is_blocked_on_a_scottish_holiday(
        self, session: Session
    ) -> None:
        """The guard used to run against England & Wales whatever was configured.

        `ClockService` built its own `BankHolidayService` inside `clock_in`, and
        that constructor defaulted the division. So two of the three regions we
        offer had it exactly backwards: allowed on their own bank holiday,
        blocked on a day they were expected at work.
        """
        services = self._configured(session, "scotland")
        at = datetime.combine(SCOTTISH_HOLIDAY, time(9), tzinfo=UTC)

        result = services.clock.clock_in(now=at)

        assert result.success is False
        assert "bank holiday" in result.message

    def test_a_scottish_user_may_work_an_english_bank_holiday(
        self, session: Session
    ) -> None:
        services = self._configured(session, "scotland")
        at = datetime.combine(ENGLISH_HOLIDAY, time(9), tzinfo=UTC)

        assert services.clock.clock_in(now=at).success is True

    def test_an_english_user_is_blocked_on_an_english_holiday(
        self, session: Session
    ) -> None:
        services = self._configured(session, "england-and-wales")
        at = datetime.combine(ENGLISH_HOLIDAY, time(9), tzinfo=UTC)

        assert services.clock.clock_in(now=at).success is False
