"""The wallet's view model: allowances, pace, and the running balance."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Portion
from flexi.domain.leaveyear import fraction_elapsed
from flexi.domain.wallet import Pace
from flexi.models.database.db import BankHolidayCache
from flexi.services.registry import (
    Services,
    available_toil_days,
    build_services,
    invalidate_services,
)
from flexi.services.settings import parse_settings
from tests.services.conftest import Configured

MONDAY = date(2026, 6, 8)
SUNDAY = date(2026, 6, 14)
THURSDAY = date(2026, 6, 11)
CONTRACTED = timedelta(minutes=444)


@pytest.fixture
def services(configure: Configured) -> Services:
    """25 days' leave, and a leave year starting on the Monday of the test week.

    The balance accumulates from the start date, so a January start would score
    five months of unworked days as deficit.
    """
    return configure(
        leave_year_start="06-08",
        entitlement=(2026, 25.0),
        holidays=((date(2026, 1, 1), "New Year's Day"),),
    )


def work(services: Services, when: date, hours: float) -> None:
    start = datetime.combine(when, datetime.min.time(), tzinfo=UTC).replace(hour=9)
    services.clock.clock_in(now=start)
    services.clock.clock_out(now=start + timedelta(hours=hours))
    invalidate_services(services)


# Allowances


def test_an_untouched_wallet_reports_the_whole_entitlement(services: Services) -> None:
    data = services.wallet.compute(MONDAY, SUNDAY, today=THURSDAY)
    annual = data.allowance(AbsenceType.ANNUAL)
    assert annual.total == 25.0
    assert annual.used == 0
    assert annual.remaining == 25.0


def test_a_booked_day_is_drawn_down(services: Services) -> None:
    services.absence.book(date(2026, 6, 10), AbsenceType.ANNUAL)
    invalidate_services(services)
    annual = services.wallet.compute(MONDAY, SUNDAY, today=THURSDAY).allowance(
        AbsenceType.ANNUAL
    )
    assert annual.used == 1.0
    assert annual.remaining == 24.0


def test_a_half_day_costs_half(services: Services) -> None:
    """A morning is half a day and one occasion."""
    services.absence.book(date(2026, 6, 10), AbsenceType.ANNUAL, Portion.AM)
    invalidate_services(services)
    annual = services.wallet.compute(MONDAY, SUNDAY, today=THURSDAY).allowance(
        AbsenceType.ANNUAL
    )
    assert annual.used == 0.5
    assert annual.remaining == 24.5
    assert annual.occurrences == 1


def test_sickness_is_counted_but_never_capped(services: Services) -> None:
    services.absence.book(date(2026, 6, 9), AbsenceType.SICK)
    invalidate_services(services)
    sick = services.wallet.compute(MONDAY, SUNDAY, today=THURSDAY).allowance(
        AbsenceType.SICK
    )
    assert sick.used == 1.0
    assert sick.total is None
    assert not sick.is_capped


def test_pace_marks_where_an_even_spread_would_be(services: Services) -> None:
    data = services.wallet.compute(MONDAY, SUNDAY, today=date(2026, 12, 8))
    annual = data.allowance(AbsenceType.ANNUAL)
    # Half the year gone and none of it spent: under the even spread, which
    # `Pace` counts as on track. Only overspending is worth a marker.
    assert annual.pace is not None
    assert 11.0 < annual.pace < 14.0
    assert annual.pace_state is Pace.ON_TRACK


def test_an_unrecorded_entitlement_is_unknown(session: Session) -> None:
    """An allowance not recorded is not an allowance spent."""
    services = build_services(session)
    services.settings.save_settings(
        parse_settings(
            leave_year_start="06-08",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    data = build_services(session).wallet.compute(MONDAY, SUNDAY, today=THURSDAY)
    assert data.allowance(AbsenceType.ANNUAL).remaining is None


# Balance


def test_the_balance_banks_overtime(services: Services) -> None:
    work(services, MONDAY, hours=9.4)
    data = services.wallet.compute(MONDAY, SUNDAY, today=MONDAY)
    assert data.balance.delta == timedelta(hours=9.4) - CONTRACTED
    assert data.balance.is_surplus


def test_the_balance_ignores_a_day_of_annual_leave(services: Services) -> None:
    services.absence.book(MONDAY, AbsenceType.ANNUAL)
    invalidate_services(services)
    data = services.wallet.compute(MONDAY, SUNDAY, today=MONDAY)
    assert data.balance.delta == timedelta()


def test_a_toil_day_spends_the_balance(services: Services) -> None:
    services.absence.book(MONDAY, AbsenceType.FLEXI)
    invalidate_services(services)
    data = services.wallet.compute(MONDAY, SUNDAY, today=MONDAY)
    assert data.balance.delta == -CONTRACTED
    assert data.balance_days == pytest.approx(-1.0)


def test_available_toil_is_the_balance_in_days(services: Services) -> None:
    work(services, MONDAY, hours=7.4 + 7.4)
    assert available_toil_days(services, MONDAY) == pytest.approx(1.0, abs=0.05)


def test_toil_booked_on_a_new_holiday_is_freed(
    services: Services, session: Session
) -> None:
    """The ledger asks nothing of a bank holiday, so it takes no TOIL on one."""
    work(services, MONDAY, hours=7.4 + 7.4)
    friday = date(2026, 6, 12)
    services.absence.book(friday, AbsenceType.FLEXI)
    invalidate_services(services)
    assert available_toil_days(services, MONDAY) == pytest.approx(0.0, abs=0.05)

    session.add(
        BankHolidayCache(
            division="england-and-wales", date=friday, title="Declared since"
        )
    )
    session.commit()
    invalidate_services(services)

    assert available_toil_days(services, MONDAY) == pytest.approx(1.0, abs=0.05)


def test_the_period_figures_cover_only_the_shown_span(services: Services) -> None:
    work(services, THURSDAY, hours=8)
    work(services, date(2026, 6, 15), hours=12)  # the Monday after the shown week
    data = services.wallet.compute(MONDAY, SUNDAY, today=date(2026, 6, 15))
    assert data.period.worked == timedelta(hours=8)
    assert data.balance.worked == timedelta(hours=20)


def test_the_leave_year_bounds_a_year(services: Services) -> None:
    start, end = services.wallet.compute(MONDAY, SUNDAY, today=THURSDAY).leave_year
    assert (start, end) == (date(2026, 6, 8), date(2027, 6, 7))


# A contracted day of nothing


def test_a_zero_contracted_day_does_not_divide_by_zero(
    services: Services, session: Session
) -> None:
    """Every wallet figure is a balance divided by the contracted day."""
    services.settings.save_settings(
        parse_settings(
            leave_year_start="06-08",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
            contracted_minutes=0,
        )
    )
    rebuilt = build_services(session)
    work(rebuilt, MONDAY, hours=8)

    assert available_toil_days(rebuilt, MONDAY) == 0.0
    assert rebuilt.wallet.compute(MONDAY, SUNDAY, today=MONDAY).balance_days == 0.0


# How far through the year we are


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 6, 8), 0.0),
        (date(2026, 6, 7), 0.0),
        (date(2027, 6, 7), 1.0),
        (date(2027, 6, 30), 1.0),
    ],
)
def test_the_elapsed_fraction_never_leaves_the_track(
    today: date, expected: float
) -> None:
    """The year calendar scrolls, so a year is seen from outside its own ends."""
    assert fraction_elapsed(date(2026, 6, 8), date(2027, 6, 7), today) == expected


def test_a_leave_year_of_one_day_is_wholly_elapsed() -> None:
    """`leaveyear.bounds` makes no such year; the clamp still guards the division."""
    day = date(2026, 6, 8)
    assert fraction_elapsed(day, day, day) == 1.0
