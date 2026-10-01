"""Every term of a day's ledger is a whole number of minutes.

A punch keeps its seconds for the audit trail and the under-a-minute rule, and
every figure is printed in whole minutes. A term carrying seconds is one two
surfaces can round two ways, so none may: each case here is a way a second
could reach a day.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest

from flexi.constants import AbsenceType, Portion
from flexi.services.registry import Services, invalidate_services
from flexi.services.settings import parse_settings
from tests.services.conftest import Configured

THURSDAY = date(2026, 6, 11)
MINUTE = timedelta(minutes=1)


def punched_with_seconds(services: Services) -> datetime:
    """In at 09:00:40, out at 17:00:20."""
    services.clock.clock_in(now=datetime(2026, 6, 11, 9, 0, 40, tzinfo=UTC))
    services.clock.clock_out(now=datetime(2026, 6, 11, 17, 0, 20, tzinfo=UTC))
    return datetime(2026, 6, 11, 18, 0, tzinfo=UTC)


def still_running(services: Services) -> datetime:
    """In at 09:00:40 and still on at 12:00:30."""
    services.clock.clock_in(now=datetime(2026, 6, 11, 9, 0, 40, tzinfo=UTC))
    return datetime(2026, 6, 11, 12, 0, 30, tzinfo=UTC)


def left_running_overnight(services: Services) -> datetime:
    """In at 20:00:30, never out, and read the next morning before any sweep."""
    services.clock.clock_in(now=datetime(2026, 6, 11, 20, 0, 30, tzinfo=UTC))
    return datetime(2026, 6, 12, 9, 0, 30, tzinfo=UTC)


def half_an_odd_minute_day(services: Services) -> datetime:
    """A TOIL morning off a 7:25 day, half of which is 3:42:30 to the second."""
    services.settings.save_settings(
        parse_settings(
            leave_year_start="10-20",
            working_days="0,1,2,3,4",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
            contracted_minutes=445,
        )
    )
    booked = services.absence.book(THURSDAY, AbsenceType.FLEXI, Portion.AM)
    assert booked.success, booked.message
    return datetime(2026, 6, 11, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "arrange",
    [
        punched_with_seconds,
        still_running,
        left_running_overnight,
        half_an_odd_minute_day,
    ],
    ids=[
        "punches with seconds",
        "a session open at 12:00:30",
        "a session left running overnight",
        "half a 7:25 day",
    ],
)
def test_every_term_of_a_day_is_whole_minutes(
    configure: Configured, arrange: Callable[[Services], datetime]
) -> None:
    services = configure()
    now = arrange(services)
    invalidate_services(services)

    day = services.ledger.day(THURSDAY, now=now)

    for term in (day.worked, day.expected, day.toil_taken, day.adjustment):
        assert term % MINUTE == timedelta(), term
