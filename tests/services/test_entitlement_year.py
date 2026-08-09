"""An allowance belongs to a leave year, not to a calendar year.

Setup filed it under `wallclock.today().year`. Set Flexi up in February against
an April leave year and the allowance landed on a year that had not started, so
get_active_entitlement_days returned None -- and _entitlement_refusal reads None
as "no limit recorded", which means annual leave was refused on nothing at all.
Anyone on a UK April leave year had an uncapped allowance until the April.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Portion
from flexi.services.registry import Services
from flexi.services.settings import SettingsService

APRIL_LEAVE_YEAR = "04-06"
BEFORE_IT_TURNS = date(2026, 2, 15)
AFTER_IT_TURNS = date(2026, 6, 11)


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
    """Why the misfiling mattered: the refusal reads None as 'no limit recorded'.

    Asserted against the refusal directly. Going through book() would be refused
    a step earlier, for want of bank holiday data, and would hide this.
    """
    _configure(session)
    services = Services.build(session)
    absence = services.absence

    assert absence.get_remaining_annual_leave(BEFORE_IT_TURNS) is None
    refusal = absence._entitlement_refusal(
        BEFORE_IT_TURNS, AbsenceType.ANNUAL, Portion.FULL
    )
    assert refusal is None, "no allowance found, so nothing is refused"
