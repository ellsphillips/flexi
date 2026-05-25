"""Tests for Slice 7: absence service.

Covers: annual/sick/flexi creation, rejection on non-working/BH/work-session days,
removal, type change, invalid markers, and balance checks.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from flexi.constants import AbsenceType
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base, BankHolidayCache
from flexi.services.absence import AbsenceService
from flexi.services.bank_holidays import BankHolidayService
from flexi.services.clock import ClockService
from flexi.services.settings import SettingsService


def _next_weekday(start: date, weekday: int) -> date:
    """Return the next date on or after start with the given weekday."""
    days_ahead = weekday - start.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return start + timedelta(days=days_ahead)


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
def settings(session) -> SettingsService:
    svc = SettingsService(session)
    svc.save_settings(
        leave_year_start="01-01",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    svc.save_entitlement(2026, 25.0)
    return svc


@pytest.fixture()
def bank_holidays(session) -> BankHolidayService:
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
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


@pytest.fixture()
def absence(session, settings, bank_holidays) -> AbsenceService:
    return AbsenceService(session, settings, bank_holidays)


# ---------- creation ----------


class TestMarkAbsence:
    def test_annual_on_working_day(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)  # Monday
        result = absence.mark_absence(d, AbsenceType.ANNUAL)
        assert result.success is True

    def test_sick_on_working_day(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 1)  # Tuesday
        result = absence.mark_absence(d, AbsenceType.SICK)
        assert result.success is True

    def test_flexi_on_working_day(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 2)  # Wednesday
        result = absence.mark_absence(d, AbsenceType.FLEXI)
        assert result.success is True


# ---------- rejections ----------


class TestRejections:
    def test_reject_non_working_day(self, absence: AbsenceService) -> None:
        saturday = _next_weekday(date(2026, 6, 8), 5)
        result = absence.mark_absence(saturday, AbsenceType.ANNUAL)
        assert result.success is False

    def test_reject_bank_holiday(self, absence: AbsenceService) -> None:
        result = absence.mark_absence(date(2026, 12, 25), AbsenceType.ANNUAL)
        assert result.success is False

    def test_reject_duplicate(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.mark_absence(d, AbsenceType.ANNUAL)
        result = absence.mark_absence(d, AbsenceType.SICK)
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
            result = svc.mark_absence(_next_weekday(date(2026, 6, 8), 0), AbsenceType.ANNUAL)
        finally:
            session.close()
            engine.dispose()

        assert result.success is False
        assert "unavailable" in result.message

    def test_reject_on_date_with_work_session(
        self, absence: AbsenceService, session
    ) -> None:
        d = _next_weekday(date(2026, 7, 6), 0)  # A Monday in future
        clock = ClockService(session)
        now = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
        clock.clock_in(now=now)
        clock.clock_out(now=now + timedelta(hours=8))

        result = absence.mark_absence(d, AbsenceType.ANNUAL)
        assert result.success is False
        assert "work sessions" in result.message


# ---------- removal ----------


class TestRemoval:
    def test_remove_existing(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.mark_absence(d, AbsenceType.SICK)
        result = absence.remove_absence(d)
        assert result.success is True
        assert absence.has_absence(d) is False

    def test_remove_nonexistent(self, absence: AbsenceService) -> None:
        result = absence.remove_absence(date(2026, 1, 2))
        assert result.success is False


# ---------- type change ----------


class TestTypeChange:
    def test_change_sick_to_annual(self, absence: AbsenceService) -> None:
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.mark_absence(d, AbsenceType.SICK)
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
        d = _next_weekday(date(2026, 6, 8), 0)
        absence.mark_absence(d, AbsenceType.ANNUAL)
        remaining = absence.get_remaining_annual_leave()
        assert remaining == 24.0

    def test_reject_when_insufficient(
        self, session, bank_holidays, settings
    ) -> None:
        settings.save_entitlement(2026, 1.0)
        svc = AbsenceService(session, settings, bank_holidays)
        d1 = _next_weekday(date(2026, 6, 8), 0)
        svc.mark_absence(d1, AbsenceType.ANNUAL)
        d2 = _next_weekday(date(2026, 6, 15), 1)
        result = svc.mark_absence(d2, AbsenceType.ANNUAL)
        assert result.success is False
        assert "Insufficient" in result.message


# ---------- counts ----------


class TestCounts:
    def test_count_by_type(self, absence: AbsenceService) -> None:
        d1 = _next_weekday(date(2026, 6, 8), 0)
        d2 = _next_weekday(date(2026, 6, 8), 1)
        absence.mark_absence(d1, AbsenceType.SICK)
        absence.mark_absence(d2, AbsenceType.SICK)
        assert absence.count_absences(AbsenceType.SICK) == 2
        assert absence.count_absences(AbsenceType.ANNUAL) == 0
