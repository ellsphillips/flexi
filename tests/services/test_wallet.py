"""The wallet's view model: allowances, pace, and the running balance."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Portion
from flexi.domain.leaveyear import fraction_elapsed
from flexi.domain.wallet import Pace
from flexi.services.registry import Services
from tests.services.conftest import Configured

MONDAY = date(2026, 6, 8)
SUNDAY = date(2026, 6, 14)
THURSDAY = date(2026, 6, 11)
CONTRACTED = timedelta(minutes=444)


@pytest.fixture
def services(configure: Configured) -> Services:
    """25 days' leave, and a leave year starting on the Monday of the test week.

    The start date is deliberate: the balance accumulates from it, so a January
    start would score five months of unworked days as deficit and every
    assertion below would be about the fixture rather than the behaviour.
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
    assert annual.pace_state is Pace.ON_TRACK


def test_an_unrecorded_entitlement_reads_as_unknown_not_zero(session: Session) -> None:
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


# -- a contracted day of nothing -------------------------------------------


def test_a_contracted_day_of_zero_is_not_a_division_by_zero(
    services: Services, session: Session
) -> None:
    """The settings screen takes the number of minutes, and 0 is typeable.

    Every figure in the wallet is a balance divided by a contracted day, so a
    zero there is a `ZeroDivisionError` on the way to drawing the sidebar —
    which is the whole application refusing to open over a settings mistake
    there is then no screen left to correct it from.
    """
    services.settings.save_settings(
        leave_year_start="06-08",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
        contracted_minutes=0,
    )
    rebuilt = Services.build(session)
    work(rebuilt, MONDAY, hours=8)

    assert rebuilt.wallet.available_toil_days(MONDAY) == 0.0
    assert rebuilt.wallet.compute(MONDAY, SUNDAY, today=MONDAY).balance_days == 0.0


# -- how far through the year we are ---------------------------------------


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
    """A pace marker past the end of a gauge reads as a rendering fault.

    A leave year can be looked at from before it starts and from after it ends —
    the year calendar scrolls — and the honest statement at each end is "none of
    it" and "all of it", not a negative marker or one off the right-hand side.
    """
    assert fraction_elapsed(date(2026, 6, 8), date(2027, 6, 7), today) == expected


def test_a_leave_year_of_one_day_is_wholly_elapsed() -> None:
    """Rather than dividing by the nothing between its ends.

    `leaveyear.bounds` cannot produce one today, but the clamp is what stops a
    future change to it taking the sidebar down with a `ZeroDivisionError`.
    """
    day = date(2026, 6, 8)
    assert fraction_elapsed(day, day, day) == 1.0
