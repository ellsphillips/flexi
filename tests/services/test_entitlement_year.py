"""An allowance belongs to a leave year, not to a calendar year.

Setup filed it under `wallclock.today().year`. Set Flexi up in February against
an April leave year and the allowance landed on a year that had not started, so
get_active_entitlement_days returned None -- and None reads as "no limit
recorded", which means annual leave was refused on nothing at all.
Anyone on a UK April leave year had an uncapped allowance until the April.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType
from flexi.models.database.db import BankHolidayCache
from flexi.services.registry import Services
from flexi.services.settings import SettingsService

APRIL_LEAVE_YEAR = "04-06"
BEFORE_IT_TURNS = date(2026, 2, 15)
A_WORKING_DAY_BEFORE_IT_TURNS = date(2026, 2, 16)
"""The Monday after. `book()` refuses a Sunday before it looks at any allowance,
which is why this used to be asserted against a private helper instead."""
AFTER_IT_TURNS = date(2026, 6, 11)


def _seed_calendar(session: Session) -> None:
    """One cached holiday, so a booking is not refused for want of a calendar.

    `book()` asks whether the day is a bank holiday before it asks about the
    allowance, and an absent calendar is a refusal in its own right.
    """
    session.add(
        BankHolidayCache(
            division="england-and-wales",
            date=date(2026, 12, 25),
            title="Christmas Day",
            fetched_at=datetime(2026, 1, 1),
        )
    )
    session.commit()


def _configure(session: Session, start: str = APRIL_LEAVE_YEAR) -> SettingsService:
    settings = SettingsService(session)
    settings.save_settings(
        leave_year_start=start,
        working_days="Mon-Fri",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    return settings


@pytest.mark.parametrize("today", [BEFORE_IT_TURNS, AFTER_IT_TURNS])
def test_the_allowance_is_found_whenever_setup_ran(
    session: Session, today: date
) -> None:
    settings = _configure(session)
    settings.save_entitlement(settings.active_leave_year(today), 25.0)

    assert settings.get_active_entitlement_days(today) == 25.0


def test_february_and_june_file_it_under_different_years(session: Session) -> None:
    """The two sides of an April turnover. Both must resolve."""
    settings = _configure(session)
    assert settings.active_leave_year(BEFORE_IT_TURNS) == 2025
    assert settings.active_leave_year(AFTER_IT_TURNS) == 2026


def test_a_january_leave_year_is_unaffected(session: Session) -> None:
    """Where the bug hid: calendar year and leave year agree, so it never showed."""
    settings = _configure(session, start="01-01")
    settings.save_entitlement(settings.active_leave_year(BEFORE_IT_TURNS), 25.0)
    assert settings.active_leave_year(BEFORE_IT_TURNS) == BEFORE_IT_TURNS.year
    assert settings.get_active_entitlement_days(BEFORE_IT_TURNS) == 25.0


def test_annual_leave_is_capped_rather_than_unlimited(session: Session) -> None:
    """The consequence. None reads as 'no limit', so the allowance must be found."""
    settings = _configure(session)
    settings.save_entitlement(settings.active_leave_year(BEFORE_IT_TURNS), 1.0)
    services = Services.build(session)

    remaining = services.absence.get_remaining_annual_leave(BEFORE_IT_TURNS)
    assert remaining == 1.0, "None here means annual leave is refused on nothing"


def test_filing_it_under_the_calendar_year_is_what_broke_it(session: Session) -> None:
    """Pins the old behaviour as wrong, so nobody reintroduces it."""
    settings = _configure(session)
    settings.save_entitlement(BEFORE_IT_TURNS.year, 25.0)  # the old call

    assert settings.get_active_entitlement_days(BEFORE_IT_TURNS) is None


def test_no_allowance_found_means_no_limit_applied(session: Session) -> None:
    """Why the misfiling mattered: None reads as "no limit recorded".

    Asserted through `book()` now, with a calendar seeded so the booking is not
    refused a step earlier for want of one. It used to call a private refusal
    helper directly -- the shape of a missing seam -- and that helper turned out
    to be dead code that only this test kept alive.
    """
    _configure(session)
    _seed_calendar(session)
    absence = Services.build(session).absence

    assert absence.get_remaining_annual_leave(BEFORE_IT_TURNS) is None

    result = absence.book(A_WORKING_DAY_BEFORE_IT_TURNS, AbsenceType.ANNUAL)

    assert result.success is True, result.message
