"""An allowance belongs to a leave year, not to a calendar year.

Filed under `wallclock.today().year`, a February setup against an April leave
year lands the allowance on a year that has not started, and
`get_active_entitlement_days` then returns None. None reads as "no limit
recorded", so annual leave would be capped by nothing until the April.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType
from flexi.models.database.db import BankHolidayCache, BankHolidayRefresh
from flexi.services.registry import build_services
from flexi.services.settings import SettingsService, parse_settings

APRIL_LEAVE_YEAR = "04-06"
BEFORE_IT_TURNS = date(2026, 2, 15)
A_WORKING_DAY_BEFORE_IT_TURNS = date(2026, 2, 16)
"""The Monday after: `book()` refuses a Sunday before it reads any allowance."""
AFTER_IT_TURNS = date(2026, 6, 11)


def _seed_calendar(session: Session) -> None:
    """One cached holiday, so a booking is not refused for want of a calendar.

    `book()` asks whether the day is a bank holiday before it asks about the
    allowance, and an absent calendar is a refusal in its own right.
    """
    session.add_all(
        (
            BankHolidayRefresh(
                division="england-and-wales",
                fetched_at=datetime(2026, 1, 1),
            ),
            BankHolidayCache(
                division="england-and-wales",
                date=date(2026, 12, 25),
                title="Christmas Day",
            ),
        )
    )
    session.commit()


def _configure(session: Session, start: str = APRIL_LEAVE_YEAR) -> SettingsService:
    settings = SettingsService(session)
    settings.save_settings(
        parse_settings(
            leave_year_start=start,
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    return settings


@pytest.mark.parametrize("today", [BEFORE_IT_TURNS, AFTER_IT_TURNS])
def test_allowance_is_found_whenever_setup_ran(session: Session, today: date) -> None:
    settings = _configure(session)
    settings.save_entitlement(settings.active_leave_year(today), 25.0)

    assert settings.get_active_entitlement_days(today) == 25.0


def test_february_and_june_file_under_different_years(session: Session) -> None:
    """The two sides of an April turnover, both of which have to resolve."""
    settings = _configure(session)
    assert settings.active_leave_year(BEFORE_IT_TURNS) == 2025
    assert settings.active_leave_year(AFTER_IT_TURNS) == 2026


def test_january_leave_year_is_unaffected(session: Session) -> None:
    """Calendar year and leave year agree, so the two spellings cannot differ."""
    settings = _configure(session, start="01-01")
    settings.save_entitlement(settings.active_leave_year(BEFORE_IT_TURNS), 25.0)
    assert settings.active_leave_year(BEFORE_IT_TURNS) == BEFORE_IT_TURNS.year
    assert settings.get_active_entitlement_days(BEFORE_IT_TURNS) == 25.0


def test_annual_leave_is_capped_by_the_allowance(session: Session) -> None:
    """None reads as 'no limit', so the allowance has to be found."""
    settings = _configure(session)
    settings.save_entitlement(settings.active_leave_year(BEFORE_IT_TURNS), 1.0)
    services = build_services(session)

    remaining = services.absence.get_remaining_annual_leave(BEFORE_IT_TURNS)
    assert remaining == 1.0, "None here means annual leave is refused on nothing"


def test_calendar_year_filing_is_not_found(session: Session) -> None:
    settings = _configure(session)
    settings.save_entitlement(BEFORE_IT_TURNS.year, 25.0)  # not the leave year

    assert settings.get_active_entitlement_days(BEFORE_IT_TURNS) is None


def test_no_allowance_found_means_no_limit_applied(session: Session) -> None:
    """None reads as "no limit recorded", so `book()` allows the day."""
    _configure(session)
    _seed_calendar(session)
    absence = build_services(session).absence

    assert absence.get_remaining_annual_leave(BEFORE_IT_TURNS) is None

    result = absence.book(A_WORKING_DAY_BEFORE_IT_TURNS, AbsenceType.ANNUAL)

    assert result.success is True, result.message
