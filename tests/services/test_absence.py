"""Booking absence, and every reason a booking is refused.

The refusals matter more than the bookings: each one is a sentence the status
bar shows unedited, so a change in wording is a change in the interface.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import time_machine
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Portion
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import BankHolidayCache, Base
from flexi.services.absence import AbsenceService
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.registry import Services
from flexi.services.settings import SettingsService


def _next_weekday(start: date, weekday: int) -> date:
    """Return the next date on or after start with the given weekday."""
    days_ahead = weekday - start.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return start + timedelta(days=days_ahead)


@pytest.fixture
def settings(session: Session) -> SettingsService:
    svc = SettingsService(session)
    svc.save_settings(
        leave_year_start="01-01",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    # The active leave year, not a fixed one. A hardcoded 2026 here is compared
    # against the real clock by get_active_entitlement_days, so the test would
    # have started failing on 1 January 2027 with nothing having changed.
    svc.save_entitlement(svc.active_leave_year(), 25.0)
    return svc


MIDSUMMER = datetime(2026, 6, 10, 12, 0)
"""The clock these tests run against.

Every date here is fixed, and several of them ask a question about "the active
leave year", which reads the real one. Left alone the two agree until the
calendar turns and then quietly stop: a booking on a 2026 date stops counting
against an allowance filed under 2027, and the suite fails on a morning when
nothing has changed.
"""


@pytest.fixture(autouse=True)
def _midsummer() -> Iterator[None]:
    with time_machine.travel(MIDSUMMER, tick=False):
        yield


@pytest.fixture
def bank_holidays(session: Session) -> BankHolidayService:
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    session.add(
        BankHolidayCache(
            division="england-and-wales",
            date=date(2026, 12, 25),
            title="Christmas Day",
            fetched_at=now,
        )
    )
    session.commit()
    return BankHolidayService(session, "england-and-wales")


@pytest.fixture
def absence(
    session: Session, settings: SettingsService, bank_holidays: BankHolidayService
) -> AbsenceService:
    return AbsenceService(session, settings, bank_holidays)


# ---------- creation ----------


class TestBooking:
    def test_annual_on_working_day(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)  # Monday
        result = absence.book(d, AbsenceType.ANNUAL)
        assert result.success is True

    def test_sick_on_working_day(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 1)  # Tuesday
        result = absence.book(d, AbsenceType.SICK)
        assert result.success is True

    def test_flexi_on_working_day(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 2)  # Wednesday
        result = absence.book(d, AbsenceType.FLEXI)
        assert result.success is True


# ---------- rejections ----------


class TestRejections:
    def test_reject_non_working_day(self, absence: AbsenceService) -> None:
        saturday = _next_weekday(date(2026, 6, 8), 5)
        result = absence.book(saturday, AbsenceType.ANNUAL)
        assert result.success is False

    def test_reject_bank_holiday(self, absence: AbsenceService) -> None:
        result = absence.book(date(2026, 12, 25), AbsenceType.ANNUAL)
        assert result.success is False

    def test_reject_duplicate(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.book(d, AbsenceType.ANNUAL)
        result = absence.book(d, AbsenceType.SICK)
        assert result.success is False

    def test_reject_when_bh_unavailable(self, tmp_path: Path) -> None:
        """It refuses rather than guess when it cannot tell if a date is a holiday.

        A fresh database, so the bank-holiday cache is genuinely empty:
        `is_bank_holiday` answers None, which is not the same as False, and
        booking leave over a bank holiday it could not see would be worse than
        refusing.
        """
        engine = create_db_engine(tmp_path / "empty.db")
        Base.metadata.create_all(engine)
        session = get_session(engine)
        try:
            settings = SettingsService(session)
            settings.save_settings(
                leave_year_start="01-01",
                working_days="0,1,2,3,4",
                bank_holiday_division="england-and-wales",
                auto_close_time="18:00",
            )
            svc = AbsenceService(
                session, settings, BankHolidayService(session, "england-and-wales")
            )
            result = svc.book(_next_weekday(date(2026, 6, 8), 0), AbsenceType.ANNUAL)
        finally:
            session.close()
            engine.dispose()

        assert result.success is False
        assert "unavailable" in result.message

    def test_reject_on_date_with_work_session(
        self, absence: AbsenceService, session: Session
    ) -> None:
        d = _next_weekday(date(2026, 7, 6), 0)  # A Monday in future
        clock = Services.build(session).clock
        now = datetime.combine(d, datetime.min.time(), tzinfo=UTC)
        clock.clock_in(now=now)
        clock.clock_out(now=now + timedelta(hours=8))

        result = absence.book(d, AbsenceType.ANNUAL)
        assert result.success is False
        assert "recorded work" in result.message

    @pytest.mark.parametrize(
        ("worked_from", "worked_to", "refused", "allowed"),
        [
            (9, 11, Portion.AM, Portion.PM),
            (14, 16, Portion.PM, Portion.AM),
        ],
    )
    def test_a_half_day_is_refused_only_over_the_half_that_was_worked(
        self,
        absence: AbsenceService,
        session: Session,
        worked_from: int,
        worked_to: int,
        refused: Portion,
        allowed: Portion,
    ) -> None:
        """`flexi leave sick today pm` is one of the command's own examples.

        `Portion.FULL` returns before any time is compared, so the whole-day
        test above never reached the comparison. Once clock events began coming
        back from `moment_of` as aware datetimes, the naive midday built beside
        them raised `TypeError: can't compare offset-naive and offset-aware`
        for every half day booked against a day with work on it.
        """
        d = _next_weekday(date(2026, 7, 6), 0)
        clock = Services.build(session).clock
        midnight = datetime.combine(d, datetime.min.time(), tzinfo=UTC)
        clock.clock_in(now=midnight.replace(hour=worked_from))
        clock.clock_out(now=midnight.replace(hour=worked_to))

        over_the_work = absence.book(d, AbsenceType.SICK, portion=refused)
        assert over_the_work.success is False
        assert "recorded work" in over_the_work.message

        the_other_half = absence.book(d, AbsenceType.SICK, portion=allowed)
        assert the_other_half.success is True, the_other_half.message


# ---------- removal ----------


class TestRemoval:
    def test_remove_existing(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.book(d, AbsenceType.SICK)
        result = absence.remove(d)
        assert result.success is True
        assert absence.has_absence(d) is False

    def test_remove_nonexistent(self, absence: AbsenceService) -> None:
        result = absence.remove(date(2026, 1, 2))
        assert result.success is False


# ---------- type change ----------


class TestTypeChange:
    def test_change_sick_to_annual(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.book(d, AbsenceType.SICK)
        result = absence.change_type(d, AbsenceType.ANNUAL)
        assert result.success is True
        assert result.absence is not None
        assert result.absence.absence_type is AbsenceType.ANNUAL

    def test_change_nonexistent_fails(self, absence: AbsenceService) -> None:
        result = absence.change_type(date(2026, 1, 2), AbsenceType.ANNUAL)
        assert result.success is False


# ---------- balance checks ----------


class TestBalance:
    def test_remaining_after_booking(self, absence: AbsenceService) -> None:
        """Asked about the leave year the day is in, not the one today is in.

        With no argument this reads the real clock, so a booking on a fixed
        2026 date stopped counting against it the moment the calendar turned.
        """
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.book(d, AbsenceType.ANNUAL)

        assert absence.get_remaining_annual_leave(d) == 24.0

    def test_reject_when_insufficient(
        self,
        session: Session,
        bank_holidays: BankHolidayService,
        settings: SettingsService,
    ) -> None:
        settings.save_entitlement(2026, 1.0)
        svc = AbsenceService(session, settings, bank_holidays)
        d1 = _next_weekday(date(2026, 6, 8), 0)
        svc.book(d1, AbsenceType.ANNUAL)
        d2 = _next_weekday(date(2026, 6, 15), 1)
        result = svc.book(d2, AbsenceType.ANNUAL)
        assert result.success is False
        assert "Not enough annual leave" in result.message


# ---------- counts ----------


class TestCounts:
    def test_count_by_type(self, absence: AbsenceService) -> None:
        d1 = _next_weekday(date(2026, 6, 8), 0)
        d2 = _next_weekday(date(2026, 6, 8), 1)
        absence.book(d1, AbsenceType.SICK)
        absence.book(d2, AbsenceType.SICK)
        assert absence.count_absences(AbsenceType.SICK) == 2
        assert absence.count_absences(AbsenceType.ANNUAL) == 0
