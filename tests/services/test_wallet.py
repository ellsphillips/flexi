"""The wallet's view model: allowances, pace, and the running balance."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from flexi.constants import AbsenceType, Portion
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base, BankHolidayCache
from flexi.services.registry import Services

MONDAY = date(2026, 6, 8)
SUNDAY = date(2026, 6, 14)
THURSDAY = date(2026, 6, 11)
CONTRACTED = timedelta(minutes=444)


@pytest.fixture()
def session(tmp_path: Path):
    engine = create_db_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    opened = get_session(engine)
    yield opened
    opened.close()


@pytest.fixture()
def services(session) -> Services:
    """A configured application: 25 days' leave, no holidays.

    The leave year starts on the Monday of the test week on purpose. The balance
    accumulates from that date, so a January start would score five months of
    unworked days as deficit and every assertion below would be about the
    fixture rather than about the behaviour under test.
    """
    settings = Services.build(session).settings
    settings.save_settings(
        leave_year_start="06-08",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    settings.save_entitlement(2026, 25.0)
    # One cached row, so `is_bank_holiday` answers False rather than "no data" —
    # which is a refusal, not an absence of holidays.
    session.add(
        BankHolidayCache(
            division="england-and-wales",
            date=date(2026, 1, 1),
            title="New Year's Day",
            fetched_at=datetime(2026, 1, 1, 9, 0),
        )
    )
    session.commit()
    return Services.build(session)


def work(services: Services, when: date, hours: float) -> None:
    start = datetime.combine(when, datetime.min.time(), tzinfo=timezone.utc).replace(
        hour=9
    )
    services.clock.clock_in(now=start)
    services.clock.clock_out(now=start + timedelta(hours=hours))
    services.invalidate()


# -- allowances ------------------------------------------------------------


def test_an_untouched_wallet_reports_the_whole_entitlement(services: Services) -> None:
    """It shows everything available and nothing spent."""
    data = services.wallet.compute(MONDAY, SUNDAY, today=THURSDAY)
    annual = data.allowance(AbsenceType.ANNUAL)
    assert annual.total == 25.0
    assert annual.used == 0
    assert annual.remaining == 25.0


def test_a_booked_day_is_drawn_down(services: Services) -> None:
    """It spends a day of annual leave when one is booked."""
    services.absence.book(date(2026, 6, 10), AbsenceType.ANNUAL)
    services.invalidate()
    annual = services.wallet.compute(MONDAY, SUNDAY, today=THURSDAY).allowance(
        AbsenceType.ANNUAL
    )
    assert annual.used == 1.0
    assert annual.remaining == 24.0


def test_a_half_day_costs_half(services: Services) -> None:
    """It counts a morning as half a day and as one occasion."""
    services.absence.book(date(2026, 6, 10), AbsenceType.ANNUAL, Portion.AM)
    services.invalidate()
    annual = services.wallet.compute(MONDAY, SUNDAY, today=THURSDAY).allowance(
        AbsenceType.ANNUAL
    )
    assert annual.used == 0.5
    assert annual.remaining == 24.5
    assert annual.occurrences == 1


def test_sickness_is_counted_but_never_capped(services: Services) -> None:
    """It reports sickness without an entitlement to run out of."""
    services.absence.book(date(2026, 6, 9), AbsenceType.SICK)
    services.invalidate()
    sick = services.wallet.compute(MONDAY, SUNDAY, today=THURSDAY).allowance(
        AbsenceType.SICK
    )
    assert sick.used == 1.0
    assert sick.total is None
    assert not sick.is_capped


def test_pace_marks_where_an_even_spread_would_be(services: Services) -> None:
    """It answers 'am I banking leave I cannot take' with a marker, not a number."""
    data = services.wallet.compute(MONDAY, SUNDAY, today=date(2026, 12, 8))
    annual = data.allowance(AbsenceType.ANNUAL)
    #  Half a year gone, none of it spent: behind pace, which is the warning.
    assert annual.pace is not None
    assert 11.0 < annual.pace < 14.0
    assert annual.ahead_of_pace is False


def test_an_unrecorded_entitlement_reads_as_unknown_not_zero(session) -> None:
    """It distinguishes 'no allowance recorded' from 'no allowance left'."""
    services = Services.build(session)
    services.settings.save_settings(
        leave_year_start="06-08",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    data = Services.build(session).wallet.compute(MONDAY, SUNDAY, today=THURSDAY)
    assert data.allowance(AbsenceType.ANNUAL).remaining is None


# -- balance ---------------------------------------------------------------


def test_the_balance_banks_overtime(services: Services) -> None:
    """It adds what was worked beyond the contract."""
    work(services, MONDAY, hours=9.4)
    data = services.wallet.compute(MONDAY, SUNDAY, today=MONDAY)
    assert data.balance.delta == timedelta(hours=9.4) - CONTRACTED
    assert data.balance.is_surplus


def test_the_balance_ignores_a_day_of_annual_leave(services: Services) -> None:
    """It neither earns nor costs flexi to take annual leave."""
    services.absence.book(MONDAY, AbsenceType.ANNUAL)
    services.invalidate()
    data = services.wallet.compute(MONDAY, SUNDAY, today=MONDAY)
    assert data.balance.delta == timedelta()


def test_a_toil_day_spends_the_balance(services: Services) -> None:
    """It withdraws a day of contracted hours when TOIL is taken."""
    services.absence.book(MONDAY, AbsenceType.FLEXI)
    services.invalidate()
    data = services.wallet.compute(MONDAY, SUNDAY, today=MONDAY)
    assert data.balance.delta == -CONTRACTED
    assert data.balance_days == pytest.approx(-1.0)


def test_available_toil_is_the_balance_in_days(services: Services) -> None:
    """It answers the question the booking modal asks."""
    work(services, MONDAY, hours=7.4 + 7.4)
    assert services.wallet.available_toil_days(MONDAY) == pytest.approx(1.0, abs=0.05)


def test_the_period_figures_cover_only_the_shown_span(services: Services) -> None:
    """It separates 'this week' from 'this leave year'."""
    work(services, THURSDAY, hours=8)
    work(services, date(2026, 6, 15), hours=12)  # the Monday after the shown week
    data = services.wallet.compute(MONDAY, SUNDAY, today=date(2026, 6, 15))
    assert data.period.worked == timedelta(hours=8)
    assert data.balance.worked == timedelta(hours=20)


def test_the_leave_year_bounds_a_year(services: Services) -> None:
    """It reports the span the allowances reset over."""
    start, end = services.wallet.compute(MONDAY, SUNDAY, today=THURSDAY).leave_year
    assert (start, end) == (date(2026, 6, 8), date(2027, 6, 7))
