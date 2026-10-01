"""A working day other than 7:24, through every figure measured against it."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from flexi.constants import AbsenceType, Portion
from flexi.domain.format import delta
from flexi.services.registry import invalidate_services
from tests.services.conftest import Configured, work

MONDAY = date(2026, 6, 8)
"""The day the leave year opens, so the week below is the whole of it."""

WEEK = tuple(MONDAY + timedelta(days=offset) for offset in range(5))


@pytest.mark.parametrize("hours", [8, 6])
def test_working_the_contracted_day_keeps_the_balance_level(
    configure: Configured, hours: int
) -> None:
    """Worked to the minute, an 8-hour day owes nothing, and nor does a 6-hour one.

    Measured against the 7:24 default, the same weeks read +3:00 and −7:00.
    """
    services = configure(leave_year_start="06-08", contracted_minutes=hours * 60)
    for day in WEEK:
        work(services, day, hours=hours)

    days = services.ledger.days(WEEK[0], WEEK[-1])
    assert [day.expected for day in days] == [timedelta(hours=hours)] * len(WEEK)
    assert [delta(day.delta) for day in days] == ["0:00"] * len(WEEK)
    assert delta(services.ledger.balance(WEEK[-1]).delta) == "0:00"


@pytest.mark.parametrize("hours", [8, 6])
def test_a_day_off_is_worth_the_contracted_day(
    configure: Configured, hours: int
) -> None:
    """TOIL spends a whole contracted day; half a day of leave expects half of one."""
    services = configure(
        leave_year_start="06-08",
        contracted_minutes=hours * 60,
        entitlement=(2026, 25.0),
    )
    assert services.absence.book(WEEK[0], AbsenceType.FLEXI).success
    assert services.absence.book(WEEK[1], AbsenceType.ANNUAL, Portion.AM).success
    invalidate_services(services)

    toil, half = services.ledger.days(WEEK[0], WEEK[1])
    assert toil.toil_taken == timedelta(hours=hours)
    assert half.expected == timedelta(hours=hours) / 2
