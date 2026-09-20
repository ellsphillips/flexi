"""A session left open overnight is closed at the configured time, not now."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest
import time_machine
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import ClockAction, EventSource
from flexi.models.database.db import WorkSession
from flexi.models.database.moment import moment_of, punched
from flexi.services.clock import ClockService
from flexi.services.startup import close_stale_sessions
from tests.services.conftest import Configured

TODAY = date(2026, 8, 11)
"""The day these tests run on, held still.

A Tuesday, so yesterday is a working Monday and both are clear of the bank
holiday `tests/services/conftest.py` seeds. `clock_in` refuses to open a
session on a holiday, and every test here opens one yesterday.
"""

YESTERDAY = TODAY - timedelta(days=1)
NOW = datetime.combine(TODAY, time(10, 0), tzinfo=UTC)


@pytest.fixture(autouse=True)
def _on_the_day() -> Iterator[None]:
    with time_machine.travel(NOW, tick=False):
        yield


@pytest.fixture
def svc(configure: Configured) -> ClockService:
    """The clock service of an application configured through `configure`."""
    return configure(leave_year_start="01-01", entitlement=(2026, 25.0)).clock


class TestStaleSessionClose:
    def test_closes_previous_day(self, svc: ClockService, session: Session) -> None:
        yesterday = NOW - timedelta(days=1)
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
        yesterday = NOW - timedelta(days=1)
        svc.clock_in(now=yesterday)
        closed = close_stale_sessions(session, time(18, 0))
        assert closed[0].clock_out_event is not None
        assert closed[0].clock_out_event.source == "system"
        assert closed[0].clock_out_event.action is ClockAction.OUT

    def test_auto_closed_flag_set(self, svc: ClockService, session: Session) -> None:
        yesterday = NOW - timedelta(days=1)
        svc.clock_in(now=yesterday)
        closed = close_stale_sessions(session, time(18, 0))
        assert closed[0].auto_closed is True

    def test_closes_only_once(self, svc: ClockService, session: Session) -> None:
        yesterday = NOW - timedelta(days=1)
        svc.clock_in(now=yesterday)
        close_stale_sessions(session, time(18, 0))
        second = close_stale_sessions(session, time(18, 0))
        assert second == []

    def test_noop_when_no_stale(self, svc: ClockService, session: Session) -> None:
        assert close_stale_sessions(session, time(18, 0)) == []

    def test_does_not_auto_close_voided_history(self, session: Session) -> None:
        """A discarded open row is history, not unfinished current work."""
        event = punched(
            ClockAction.IN,
            datetime.combine(YESTERDAY, time(9), tzinfo=UTC),
            source=EventSource.USER,
        )
        session.add(event)
        session.flush()
        discarded = WorkSession(
            clock_in_id=event.id,
            work_date=YESTERDAY,
            voided=True,
        )
        session.add(discarded)
        session.commit()

        assert close_stale_sessions(session, time(18, 0)) == []
        session.refresh(discarded)
        assert discarded.clock_out_id is None


class TestFallbackTo2359:
    def test_close_before_clock_in_uses_2359(
        self, svc: ClockService, session: Session
    ) -> None:
        # Clock in at 20:00 yesterday, auto-close configured at 18:00
        yesterday_8pm = datetime.combine(
            YESTERDAY,
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

    def test_clock_in_after_2359_closes_at_the_clock_in(
        self, svc: ClockService, session: Session
    ) -> None:
        """A clock-out cannot precede its own clock-in.

        23:59:30 is past the fallback, and a fallback that ignores it makes the
        segment negative.
        """
        yesterday_late = datetime.combine(YESTERDAY, time(23, 59, 30), tzinfo=UTC)
        svc.clock_in(now=yesterday_late)

        closed = close_stale_sessions(session, time(18, 0))

        closing = closed[0].clock_out_event
        assert closing is not None
        assert closing.timestamp >= closed[0].clock_in_event.timestamp
        assert closing.timestamp.time() == time(23, 59, 30)

    def test_clock_in_at_auto_close_closes_at_that_time(
        self, svc: ClockService, session: Session
    ) -> None:
        yesterday_6pm = datetime.combine(
            YESTERDAY,
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
            YESTERDAY,
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
        assert duration > 0
        assert duration == 9 * 3600  # 9am to 18:00


def test_sweep_can_be_told_what_day_it_is(svc: ClockService, session: Session) -> None:
    """`today` is a parameter so a caller can sweep as at a date it chooses.

    `run_startup_cleanup` lets it default to the wall clock; the auto-close
    backfill in `flexi init` sweeps as at the day it is migrating.
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


def test_session_closed_mid_sweep_is_not_reported(
    svc: ClockService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep reads the stale sessions, then closes them one at a time.

    Another writer, such as a second copy of the application starting, can
    close one in between. The conditional update declines, and the sweep
    reports only the clock-outs it wrote.
    """
    yesterday = NOW - timedelta(days=1)
    svc.clock_in(now=yesterday)
    monkeypatch.setattr(
        "flexi.services.startup.stage_clock_out",
        lambda *_args, **_kwargs: False,
    )

    assert close_stale_sessions(session, time(18, 0)) == []


def test_a_timezone_change_cannot_auto_close_before_clock_in(
    svc: ClockService, session: Session
) -> None:
    with wallclock.pinned(timezone(-timedelta(hours=12))):
        assert svc.clock_in(now=datetime.combine(YESTERDAY, time(23, 59, 30))).success

    [closed] = close_stale_sessions(session, time(18), today=TODAY)

    assert closed.clock_out_event is not None
    assert moment_of(closed.clock_out_event) == moment_of(closed.clock_in_event)


class TestASessionDatedAfterToday:
    """A session dated in the future, left behind by a clock that was ahead.

    `clock in` answers "Already clocked in" and the sweep closes only days that
    have been and gone, so clocking out is the only way back.
    """

    def test_sweep_leaves_it_alone(self, svc: ClockService) -> None:
        """A day that has not happened is not a day that was left open."""
        svc.clock_in(now=NOW + timedelta(days=3))

        svc.sweep()

        assert svc.is_clocked_in()

    def test_clocking_out_closes_it_and_names_the_day(self, svc: ClockService) -> None:
        svc.clock_in(now=NOW + timedelta(days=3))

        result = svc.clock_out()

        assert result.success, result.message
        assert "Fri 14 Aug 2026" in result.message
        assert not svc.is_clocked_in()

    def test_its_hours_are_discarded(self, svc: ClockService, session: Session) -> None:
        """The hours were read off a clock that was wrong.

        The events stay (they are immutable, and the day can be put back with a
        correction), but nothing derived from the session counts them.
        """
        svc.clock_in(now=NOW + timedelta(days=3))

        svc.clock_out()

        closed = session.query(WorkSession).one()
        assert closed.voided is True
        assert closed.clock_out_id is not None

    def test_clocking_in_works_again_afterwards(self, svc: ClockService) -> None:
        svc.clock_in(now=NOW + timedelta(days=3))
        svc.clock_out()

        assert svc.clock_in().success is True
