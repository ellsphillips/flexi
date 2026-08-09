"""Booking a span: partial by design, and honest about what it skipped."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from flexi.constants import AbsenceType, Portion
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import BankHolidayCache, Base
from flexi.services.registry import Services

MONDAY = date(2026, 8, 10)
FRIDAY = date(2026, 8, 14)
NEXT_FRIDAY = date(2026, 8, 21)
BANK_HOLIDAY = date(2026, 8, 31)


@pytest.fixture
def session(tmp_path: Path):
    engine = create_db_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    opened = get_session(engine)
    yield opened
    opened.close()


@pytest.fixture
def services(session) -> Services:
    built = Services.build(session)
    built.settings.save_settings(
        leave_year_start="10-20",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    built.settings.save_entitlement(2025, 25.0)
    session.add(
        BankHolidayCache(
            division="england-and-wales",
            date=BANK_HOLIDAY,
            title="Summer bank holiday",
            fetched_at=datetime(2026, 1, 1, 9, 0),
        )
    )
    session.commit()
    return Services.build(session)


def test_a_working_week_books_five_days(services: Services) -> None:
    """Monday to Friday is five, not seven."""
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 5
    assert not result.skipped


def test_weekends_are_skipped_quietly(services: Services) -> None:
    """Nobody booking a fortnight means to book the Saturdays.

    Reporting them as refusals would bury the one refusal that matters.
    """
    result = services.absence.book_range(MONDAY, NEXT_FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 10
    assert not result.skipped
    assert "10 days" in result.message("booked")


def test_a_bank_holiday_is_skipped_quietly_too(services: Services) -> None:
    """It is not yours to book, and it is not a mistake that you tried."""
    result = services.absence.book_range(
        BANK_HOLIDAY, BANK_HOLIDAY + timedelta(days=4), AbsenceType.ANNUAL
    )
    assert BANK_HOLIDAY not in result.booked
    assert len(result.booked) == 4
    assert not result.skipped


def test_a_day_already_booked_is_reported(services: Services) -> None:
    """This one is worth saying: something is there that you did not expect."""
    services.absence.book(date(2026, 8, 12), AbsenceType.SICK)
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 4
    assert [when for when, _ in result.skipped] == [date(2026, 8, 12)]
    assert "already booked" in result.message("booked")


def test_running_out_of_leave_mid_range_books_what_it_can(services: Services) -> None:
    """Partial, and it says how far it got."""
    services.settings.save_entitlement(2025, 2.0)
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 2
    assert len(result.skipped) == 3
    assert "Not enough annual leave" in result.message("booked")


def test_toil_across_a_range_warns_once(services: Services) -> None:
    """Not five times for five days."""
    result = services.absence.book_range(
        MONDAY, FRIDAY, AbsenceType.FLEXI, available_toil_days=1.0
    )
    assert len(result.booked) == 5
    assert result.warning is not None
    assert "deficit" in result.warning


def test_half_days_across_a_range(services: Services) -> None:
    """Five mornings is two and a half days of leave."""
    services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL, Portion.AM)
    used = services.absence.count_days(AbsenceType.ANNUAL, MONDAY, FRIDAY)
    assert used == 2.5


def test_clearing_a_range_removes_what_is_there(services: Services) -> None:
    """And says what it removed, not what was already free."""
    services.absence.book_range(MONDAY, date(2026, 8, 12), AbsenceType.ANNUAL)
    result = services.absence.clear_range(MONDAY, FRIDAY)
    assert len(result.booked) == 3
    assert not result.skipped
    assert services.absence.in_range(MONDAY, FRIDAY) == []


def test_clearing_an_empty_range_says_so(services: Services) -> None:
    result = services.absence.clear_range(MONDAY, FRIDAY)
    assert not result.success
    assert result.message("removed") == "Nothing to do"


def test_a_single_day_reads_as_a_day(services: Services) -> None:
    """'1 day booked', not '1 days booked'."""
    result = services.absence.book_range(MONDAY, MONDAY, AbsenceType.SICK)
    assert result.message("booked") == "1 day booked"
