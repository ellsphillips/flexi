"""Clocking in and out writes both rows, or neither.

A ClockEvent without its WorkSession is a session that never ends; the reverse
is a session with no start. Every rejected action is checked for writing
nothing at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import Mock

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import AbsenceType, ClockAction, EventSource, Portion
from flexi.models.database.db import (
    BankHolidayCache,
    BankHolidayRefresh,
    ClockEvent,
    WorkSession,
)
from flexi.models.database.moment import punched
from flexi.services.absence import AbsenceResult
from flexi.services.adjustments import AdjustmentResult
from flexi.services.clock import ClockResult, ClockService
from flexi.services.outcome import Outcome
from flexi.services.registry import Services, build_services
from flexi.services.settings import parse_settings

SCOTTISH_HOLIDAY = date(2027, 1, 4)
"""2 January, observed. Scotland only."""

ENGLISH_HOLIDAY = date(2027, 5, 3)
"""Early May. England & Wales only."""


@pytest.fixture
def svc(session: Session) -> ClockService:
    return build_services(session).clock


# ---------- accepted actions persist ----------


class TestClockIn:
    def test_creates_event_and_session(
        self, svc: ClockService, session: Session
    ) -> None:
        result = svc.clock_in()
        assert result.success is True
        assert result.at is not None, "a clock-in records the moment it recorded"
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
        started = datetime.now(tz=UTC)
        svc.clock_in(now=started)
        # An hour, not an instant: clocking straight back out is treated as a
        # slip of the finger and discarded.
        result = svc.clock_out(now=started + timedelta(hours=1))
        assert result.success is True
        assert result.at is not None, "a clock-out records the moment it recorded"
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
        assert result.at is None, "nothing was recorded, so there is no moment"
        # Only one event from the first clock-in
        events = session.execute(select(ClockEvent)).scalars().all()
        assert len(events) == 1

    def test_clock_out_without_open_session(
        self, svc: ClockService, session: Session
    ) -> None:
        result = svc.clock_out()
        assert result.success is False
        assert result.at is None, "nothing was recorded, so there is no moment"
        events = session.execute(select(ClockEvent)).scalars().all()
        assert len(events) == 0

    def test_clock_in_after_clock_out(self, svc: ClockService) -> None:
        svc.clock_in()
        svc.clock_out()
        result = svc.clock_in()
        assert result.success is True

    def test_voided_open_rows_are_not_live_sessions(
        self, svc: ClockService, session: Session
    ) -> None:
        """Discarded history may be open without joining the active clock."""
        active = svc.clock_in(now=datetime(2026, 8, 10, 9, tzinfo=UTC))
        assert active.session is not None
        discarded_event = punched(
            ClockAction.IN,
            datetime(2026, 8, 9, 9, tzinfo=UTC),
            source=EventSource.USER,
        )
        session.add(discarded_event)
        session.flush()
        discarded = WorkSession(
            clock_in_id=discarded_event.id,
            work_date=date(2026, 8, 9),
            voided=True,
        )
        session.add(discarded)
        session.commit()

        assert svc.get_open_session() is active.session
        assert svc.clock_out(now=datetime(2026, 8, 10, 17, tzinfo=UTC)).success
        session.refresh(discarded)
        assert discarded.clock_out_id is None


# ---------- rollback leaves no partial state ----------


class TestRollback:
    def test_commit_failure_rolls_back_completely(
        self,
        svc: ClockService,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A commit that fails after the flush leaves no partial state."""
        rollback = Mock(wraps=session.rollback)
        monkeypatch.setattr(session, "commit", Mock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(session, "rollback", rollback)

        with pytest.raises(RuntimeError, match="boom"):
            svc.clock_in()

        # One rollback ends the read-only preflight transaction before the
        # write reservation; the second recovers the failed commit.
        assert rollback.call_count == 2
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


class TestBankHolidayDivision:
    """The guard has to follow the configured division, not the default one."""

    @staticmethod
    def _configured(session: Session, division: str) -> Services:
        built = build_services(session)
        built.settings.save_settings(
            parse_settings(
                leave_year_start="04-06",
                working_days="0,1,2,3,4,5,6",
                bank_holiday_division=division,
                auto_close_time="18:00",
            )
        )
        stamped = datetime.now(UTC).replace(tzinfo=None)
        for when, owner in (
            (SCOTTISH_HOLIDAY, "scotland"),
            (ENGLISH_HOLIDAY, "england-and-wales"),
        ):
            session.add_all(
                (
                    BankHolidayRefresh(division=owner, fetched_at=stamped),
                    BankHolidayCache(date=when, title="test", division=owner),
                )
            )
        session.commit()
        return build_services(session)

    def test_scottish_user_blocked_on_scottish_holiday(self, session: Session) -> None:
        services = self._configured(session, "scotland")
        at = datetime.combine(SCOTTISH_HOLIDAY, time(9), tzinfo=UTC)

        result = services.clock.clock_in(now=at)

        assert result.success is False
        assert "bank holiday" in result.message

    def test_scottish_user_works_english_holiday(self, session: Session) -> None:
        services = self._configured(session, "scotland")
        at = datetime.combine(ENGLISH_HOLIDAY, time(9), tzinfo=UTC)

        assert services.clock.clock_in(now=at).success is True

    def test_english_user_blocked_on_english_holiday(self, session: Session) -> None:
        services = self._configured(session, "england-and-wales")
        at = datetime.combine(ENGLISH_HOLIDAY, time(9), tzinfo=UTC)

        assert services.clock.clock_in(now=at).success is False


def test_every_result_satisfies_the_outcome_protocol() -> None:
    """The status bar takes any result as an `Outcome`, so the shape is checked."""
    for result in (
        ClockResult(success=True, message="Clocked in"),
        AbsenceResult(success=False, message="no"),
        AdjustmentResult(success=True, message="adjusted"),
    ):
        assert isinstance(result, Outcome)
        assert isinstance(result.success, bool)
        assert isinstance(result.message, str)
        assert result.warning is None or isinstance(result.warning, str)


# ---------- a day that is only half off ----------


@pytest.fixture
def ready(session: Session) -> Services:
    """A configured install with a calendar, so absences can be booked at all."""
    built = build_services(session)
    built.settings.save_settings(
        parse_settings(
            leave_year_start="04-06",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    session.add_all(
        (
            BankHolidayRefresh(
                division="england-and-wales",
                fetched_at=datetime(2026, 1, 1, 9, 0),
            ),
            BankHolidayCache(
                division="england-and-wales",
                date=date(2026, 8, 31),
                title="Summer bank holiday",
            ),
        )
    )
    session.commit()
    rebuilt = build_services(session)
    rebuilt.settings.save_entitlement(rebuilt.settings.active_leave_year(), 25.0)
    return rebuilt


TUESDAY = date(2026, 8, 25)


def test_two_half_day_absences_refuse_the_clock(ready: Services) -> None:
    """`AbsenceService` permits two half-day bookings, so the lookup takes two rows."""
    ready.absence.book(TUESDAY, AbsenceType.SICK, Portion.AM)
    ready.absence.book(TUESDAY, AbsenceType.ANNUAL, Portion.PM)
    assert len(ready.absence.for_date(TUESDAY)) == 2

    result = ready.clock.clock_in(now=datetime(2026, 8, 25, 9, 0))

    assert result.success is False
    assert result.message == "Cannot clock in on an absence day"


def test_full_day_off_refuses_the_clock(ready: Services) -> None:
    ready.absence.book(TUESDAY, AbsenceType.ANNUAL, Portion.FULL)

    result = ready.clock.clock_in(now=datetime(2026, 8, 25, 9, 0))

    assert result.success is False
    assert result.message == "Cannot clock in on an absence day"
    assert ready.clock.get_open_session() is None


def test_half_day_off_leaves_the_other_half(ready: Services) -> None:
    """A half day may be booked over work, so work may follow a booked half."""
    ready.absence.book(TUESDAY, AbsenceType.SICK, Portion.AM)

    result = ready.clock.clock_in(now=datetime(2026, 8, 25, 13, 0))

    assert result.success is True, result.message
    assert ready.clock.get_open_session() is not None


def test_clocking_into_a_booked_half_is_refused(ready: Services) -> None:
    """A booked morning worked as well is paid for twice.

    Once out of the leave balance and once into the flexi balance. `correct`
    draws the same line.
    """
    ready.absence.book(TUESDAY, AbsenceType.SICK, Portion.AM)

    result = ready.clock.clock_in(now=datetime(2026, 8, 25, 9, 0))

    assert result.success is False
    assert result.message == "Cannot clock in during a booked morning"
    assert ready.clock.get_open_session() is None


@pytest.mark.parametrize(
    ("portion", "accepted"), [(Portion.AM, True), (Portion.PM, False)]
)
def test_noon_belongs_to_the_afternoon(
    ready: Services, portion: Portion, accepted: bool
) -> None:
    assert ready.absence.book(TUESDAY, AbsenceType.SICK, portion).success

    result = ready.clock.clock_in(now=datetime(2026, 8, 25, 12, 0))

    assert result.success is accepted
    assert ready.clock.is_clocked_in() is accepted


# ---------- losing a race to another writer ----------


class TestConcurrentWriters:
    """The stale-read arms, which only a second writer can reach.

    SQLite has no suitable row lock, so the write is conditional and the
    database is the authority. These are the two paths where it declines.
    """

    def test_lost_insert_race_is_refused(
        self, svc: ClockService, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ON CONFLICT DO NOTHING` turns the loser into `None`.

        The partial unique index alone would raise `IntegrityError`.
        """
        assert svc.clock_in().success is True
        monkeypatch.setattr(ClockService, "get_open_session", lambda _self: None)

        result = svc.clock_in()

        assert result.success is False
        assert result.message == "Already clocked in"
        assert len(session.execute(select(WorkSession)).scalars().all()) == 1
        assert len(session.execute(select(ClockEvent)).scalars().all()) == 1, (
            "the speculative IN event goes with the session it could not open"
        )

    def test_lost_update_race_is_refused(
        self, svc: ClockService, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The conditional UPDATE is what closes a session.

        The candidate OUT event is discarded with it, so committing cannot
        leave an audit row belonging to nothing.
        """
        opened = svc.clock_in()
        assert opened.session is not None
        stale = opened.session
        assert svc.clock_out().success is True
        monkeypatch.setattr(ClockService, "get_open_session", lambda _self: stale)

        result = svc.clock_out()

        assert result.success is False
        assert result.message == "Not clocked in"
        assert len(session.execute(select(ClockEvent)).scalars().all()) == 2


@pytest.mark.parametrize(
    "rewound",
    [
        datetime(2026, 8, 10, 9, 30, tzinfo=UTC),
        datetime(2026, 8, 10, 9, tzinfo=UTC),
        datetime(2026, 8, 10, 8, tzinfo=UTC),
        datetime(2026, 8, 9, 12, tzinfo=UTC),
    ],
)
def test_clock_rollback_cannot_reopen_recorded_time(
    svc: ClockService, session: Session, rewound: datetime
) -> None:
    assert svc.clock_in(now=datetime(2026, 8, 10, 9, tzinfo=UTC)).success
    assert svc.clock_out(now=datetime(2026, 8, 10, 10, tzinfo=UTC)).success

    result = svc.clock_in(now=rewound)

    assert not result.success
    assert "check your system clock" in result.message
    assert result.session is None
    assert result.at is None
    assert svc.get_open_session() is None
    assert len(session.scalars(select(ClockEvent)).all()) == 2
    assert len(session.scalars(select(WorkSession)).all()) == 1


def test_clock_can_start_exactly_when_recorded_work_ends(svc: ClockService) -> None:
    boundary = datetime(2026, 8, 10, 10, tzinfo=UTC)
    assert svc.clock_in(now=boundary - timedelta(hours=1)).success
    assert svc.clock_out(now=boundary).success
    assert svc.clock_in(now=boundary).success
    assert svc.clock_out(now=boundary + timedelta(minutes=30)).success
    assert len(svc.segments_on(boundary.date())) == 2


def test_discarded_session_does_not_block_an_earlier_clock_in(
    svc: ClockService,
) -> None:
    started = datetime(2026, 8, 10, 10, tzinfo=UTC)
    assert svc.clock_in(now=started).success
    assert svc.clock_out(now=started + timedelta(seconds=1)).success
    assert svc.clock_in(now=started - timedelta(minutes=30)).success


def test_clock_rollback_cannot_overlap_a_corrected_session(svc: ClockService) -> None:
    with time_machine.travel(datetime(2026, 8, 10, 12, tzinfo=UTC), tick=False):
        assert svc.correct(date(2026, 8, 10), time(9), time(10)).success
    assert not svc.clock_in(now=datetime(2026, 8, 10, 9, 30, tzinfo=UTC)).success
    assert svc.clock_in(now=datetime(2026, 8, 10, 10, tzinfo=UTC)).success


@pytest.mark.usefixtures("in_london")
def test_dst_fallback_can_start_a_later_session_at_an_earlier_wall_time(
    svc: ClockService,
) -> None:
    # 01:30-01:50 BST precedes 01:10-01:30 GMT despite the displayed readings.
    assert svc.clock_in(now=datetime(2026, 10, 25, 0, 30, tzinfo=UTC)).success
    assert svc.clock_out(now=datetime(2026, 10, 25, 0, 50, tzinfo=UTC)).success
    assert svc.clock_in(now=datetime(2026, 10, 25, 1, 10, tzinfo=UTC)).success
    assert svc.clock_out(now=datetime(2026, 10, 25, 1, 30, tzinfo=UTC)).success
