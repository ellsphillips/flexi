"""The balance as it stands: the leave year to yesterday, and today so far.

Today is not over. The contracted hours it has still to be worked are held back
until it ends, and whatever it has gained counts at once, so a morning opens on
the balance the evening before closed on.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
import time_machine

from flexi.services.registry import Services, available_toil_days, invalidate_services
from tests.services.conftest import Configured

MONDAY = date(2026, 9, 7)
FRIDAY = date(2026, 9, 11)
NEXT_MONDAY = date(2026, 9, 14)
CONTRACTED = timedelta(minutes=444)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def worked(services: Services, day: date, start: time, end: time) -> None:
    services.clock.clock_in(now=at(day, start.hour, start.minute))
    services.clock.clock_out(now=at(day, end.hour, end.minute))
    invalidate_services(services)


def week_of(services: Services, monday: date, start: time, end: time) -> None:
    for offset in range(5):
        worked(services, monday + timedelta(days=offset), start, end)


@pytest.fixture
def tracked(configure: Configured) -> Services:
    """Set up on the Monday of the first week, 09:00 to 16:30 each day of it."""
    services = configure(tracking_since=MONDAY)
    week_of(services, MONDAY, time(9), time(16, 30))
    return services


def balance_at(services: Services, moment: datetime) -> timedelta:
    with time_machine.travel(moment, tick=False):
        return services.ledger.balance().as_shown().delta


def test_a_morning_opens_on_the_balance_the_evening_closed_on(
    tracked: Services,
) -> None:
    """Five days of 7:30 is +0:30, and still is before Monday's first punch."""
    assert balance_at(tracked, at(FRIDAY, 23)) == timedelta(minutes=30)
    assert balance_at(tracked, at(NEXT_MONDAY, 8)) == timedelta(minutes=30)


def test_a_surplus_counts_as_soon_as_it_is_worked(tracked: Services) -> None:
    tracked.clock.clock_in(now=at(NEXT_MONDAY, 8))
    invalidate_services(tracked)

    assert balance_at(tracked, at(NEXT_MONDAY, 15)) == timedelta(minutes=30)
    assert balance_at(tracked, at(NEXT_MONDAY, 18)) == timedelta(hours=3, minutes=6)


def test_a_shortfall_counts_once_the_day_is_over(tracked: Services) -> None:
    worked(tracked, NEXT_MONDAY, time(9), time(14))

    assert balance_at(tracked, at(NEXT_MONDAY, 23, 59)) == timedelta(minutes=30)
    assert balance_at(tracked, at(NEXT_MONDAY + timedelta(days=1), 0)) == (
        timedelta(minutes=30) + timedelta(hours=5) - CONTRACTED
    )


def test_the_first_day_of_a_leave_year_reads_none_of_the_last(
    configure: Configured,
) -> None:
    """The year before closed at +0:30; the new one opens on nothing, not −7:24."""
    services = configure(leave_year_start="09-14", tracking_since=MONDAY)
    week_of(services, MONDAY, time(9), time(16, 30))

    assert balance_at(services, at(FRIDAY, 23)) == timedelta(minutes=30)
    assert balance_at(services, at(NEXT_MONDAY, 8)) == timedelta()


def test_a_banked_day_can_be_taken_in_the_morning(configure: Configured) -> None:
    """A week of +1:30 a day is a day of TOIL at 08:45 on the Monday after."""
    services = configure(tracking_since=NEXT_MONDAY)
    week_of(services, NEXT_MONDAY, time(8, 30), time(17, 24))
    monday = NEXT_MONDAY + timedelta(weeks=1)

    with time_machine.travel(at(monday, 8, 45), tick=False):
        free = available_toil_days(services, monday)

    assert free == timedelta(hours=7, minutes=30) / CONTRACTED
