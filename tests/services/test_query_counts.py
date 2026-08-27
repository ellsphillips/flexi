"""What the read paths cost in round trips, pinned so they cannot drift back.

Every figure here was once linear in the length of the span. The rules read the
database for themselves, one date at a time -- a settings row, two bank-holiday
rows, an absence row and a session row each -- so planning a year of leave was
better than two thousand queries to answer a question about 365 dates, and
clearing one was 415 queries and 25 separate commits.

A ceiling rather than an equality: an assertion on an exact count fails on any
change that adds a query anywhere, including a correct one, and a test nobody
can read the intent of gets its number bumped rather than investigated. What
must not happen is the count growing *with the span*, so the year and the
fortnight are both measured and the year is held to the same ceiling.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType
from flexi.services.registry import Services
from tests.services.conftest import Configured

YEAR_START = date(2026, 6, 15)
YEAR_END = date(2027, 6, 14)
FORTNIGHT_END = YEAR_START + timedelta(days=13)

PLAN_CEILING = 20
"""A span costs four reads and a handful of fixed lookups, whatever its length."""

WALLET_CEILING = 35
CLEAR_CEILING = 4


@contextmanager
def counting(session: Session) -> Iterator[list[str]]:
    """Every statement the session sends while the block runs."""
    seen: list[str] = []
    bind = session.get_bind()

    def record(*args: object) -> None:
        seen.append(str(args[2]))

    event.listen(bind, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(bind, "before_cursor_execute", record)


@pytest.fixture
def booked(configure: Configured) -> Services:
    """A leave year with twenty-five days of annual leave against it."""
    services = configure(entitlement=(2026, 25.0))
    when, taken = YEAR_START, 0
    while taken < 25:
        # Counting what was written, not what was attempted: the fixture's own
        # bank holiday falls on one of these Mondays and is refused.
        taken += services.absence.book(when, AbsenceType.ANNUAL).success
        when += timedelta(days=7)
    return services


def test_planning_a_year_costs_no_more_than_planning_a_fortnight(
    booked: Services, session: Session
) -> None:
    """The whole point: the cost is the number of *questions*, not of dates."""
    with counting(session) as fortnight:
        booked.absence.plan(YEAR_START, FORTNIGHT_END, AbsenceType.ANNUAL)
    with counting(session) as year:
        booked.absence.plan(YEAR_START, YEAR_END, AbsenceType.ANNUAL)

    assert len(year) == len(fortnight), "the span is being walked a query at a time"
    assert len(year) <= PLAN_CEILING, "\n".join(year)


def test_the_wallet_reads_the_leave_year_once(
    booked: Services, session: Session
) -> None:
    """It asked for days and occurrences separately, per type: ten scans.

    Each of those then validated every row it had read with three queries of
    its own, so the twenty-five rows here cost 162 round trips to produce ten
    pairs of numbers.
    """
    with counting(session) as seen:
        booked.wallet.compute(YEAR_START, FORTNIGHT_END, today=YEAR_START)

    assert len(seen) <= WALLET_CEILING, "\n".join(seen)


def test_clearing_a_year_uses_bounded_reads_and_one_commit(
    booked: Services, session: Session
) -> None:
    """Planning and locked revalidation stay constant however long the span."""
    with counting(session) as seen:
        cleared = booked.absence.clear_range(YEAR_START, YEAR_END)

    assert len(cleared.booked) == 25
    assert len(seen) <= CLEAR_CEILING, "\n".join(seen)
