"""Settling a balance without deleting the records that made it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import time_machine

from flexi import wallclock
from flexi.services.adjustments import OPENING_BALANCE
from flexi.services.registry import Services
from tests.conftest import sessions_on
from tests.services.conftest import CONTRACTED, Configured, work

MONDAY = date(2026, 6, 8)
FRIDAY = date(2026, 6, 12)
NEW_YEAR = ((date(2026, 1, 1), "New Year's Day"),)
"""A holiday well away from the test week, so the calendar answers rather than
saying it has no data."""


@pytest.fixture
def services(configure: Configured) -> Services:
    """A leave year that starts on the Monday of the test week."""
    return configure(
        leave_year_start="06-08", holidays=((date(2026, 1, 1), "New Year's Day"),)
    )


# -- the arithmetic --------------------------------------------------------


def test_an_adjustment_moves_the_balance(services: Services) -> None:
    """It is counted like any other term in the sum."""
    work(services, MONDAY, hours=7.4)
    services.adjustments.record(MONDAY, timedelta(hours=3), "carried over")
    services.invalidate()
    assert services.ledger.balance(MONDAY).delta == timedelta(hours=3)


def test_it_only_counts_from_the_date_it_takes_effect(services: Services) -> None:
    """A correction dated Friday does not move Monday's balance."""
    services.adjustments.record(FRIDAY, timedelta(hours=5), "carried over")
    services.invalidate()
    assert services.ledger.balance(MONDAY).adjustment == timedelta()
    assert services.ledger.balance(FRIDAY).adjustment == timedelta(hours=5)


def test_the_summary_reports_it_separately(services: Services) -> None:
    """A settled balance has to be able to say it was settled."""
    services.adjustments.record(MONDAY, timedelta(hours=2), "carried over")
    services.invalidate()
    summary = services.ledger.summary(MONDAY, FRIDAY)
    assert summary.adjustment == timedelta(hours=2)
    assert summary.worked == timedelta()


def test_adjustments_add_up(services: Services) -> None:
    """Two corrections on one day are one correction."""
    services.adjustments.record(MONDAY, timedelta(hours=2), "carried over")
    services.adjustments.record(MONDAY, timedelta(hours=-1), "and back again")
    services.invalidate()
    assert services.ledger.balance(MONDAY).adjustment == timedelta(hours=1)


# -- the refusals ----------------------------------------------------------


def test_an_adjustment_needs_a_reason(services: Services) -> None:
    """A correction nobody can explain is one nobody can undo with confidence."""
    result = services.adjustments.record(MONDAY, timedelta(hours=1), "   ")
    assert not result.success
    assert "reason" in result.message


def test_a_zero_adjustment_is_refused(services: Services) -> None:
    """It would be a row that says nothing."""
    result = services.adjustments.record(MONDAY, timedelta(seconds=20), "rounding")
    assert not result.success
    assert "zero minutes" in result.message


def test_removing_something_that_is_not_there_says_so(services: Services) -> None:
    """The command line takes an id typed by hand, so it takes wrong ones too."""
    result = services.adjustments.remove(404)
    assert not result.success
    assert result.message == "No such adjustment"


def test_removing_one_puts_the_balance_back(services: Services) -> None:
    """One row in, one row out."""
    recorded = services.adjustments.record(MONDAY, timedelta(hours=4), "carried over")
    assert recorded.adjustment is not None
    services.invalidate()
    assert services.ledger.balance(MONDAY).adjustment == timedelta(hours=4)

    services.adjustments.remove(recorded.adjustment.id)
    services.invalidate()
    assert services.ledger.balance(MONDAY).adjustment == timedelta()


# -- reading them back -----------------------------------------------------


def test_every_correction_ever_made_is_listed_newest_first(
    services: Services,
) -> None:
    """The recent one is the one somebody is looking for.

    `flexi balance log` prints this list in the order it comes back.
    """
    services.adjustments.record(MONDAY, timedelta(hours=2), "carried over")
    services.adjustments.record(FRIDAY, timedelta(hours=-1), "and back again")

    assert [row.date for row in services.adjustments.all()] == [FRIDAY, MONDAY]


# -- zeroing ---------------------------------------------------------------


def test_zeroing_settles_the_balance_to_the_given_date(services: Services) -> None:
    """It leaves the balance reading nothing at the end of that day."""
    work(services, MONDAY, hours=2)  # a short day: 2h worked against 7h24
    services.invalidate()
    assert services.ledger.balance(MONDAY).delta != timedelta()

    result = services.zero_balance(MONDAY)
    assert result.success
    assert services.ledger.balance(MONDAY).delta == timedelta()


def test_zeroing_leaves_the_next_day_behaving_normally(services: Services) -> None:
    """Settling is a line under the past, not a change to how days are counted."""
    work(services, MONDAY, hours=2)
    services.zero_balance(MONDAY)
    services.invalidate()

    tuesday = MONDAY + timedelta(days=1)
    work(services, tuesday, hours=9.4)
    services.invalidate()
    assert services.ledger.balance(tuesday).delta == timedelta(hours=9.4) - CONTRACTED


def test_zeroing_defaults_to_yesterday(services: Services) -> None:
    """Today is not over.

    Absorbing today's contracted hours before they have been worked would leave
    the evening looking like unearned overtime.

    The whole body used to sit inside `if result.success:`, so the two failures
    it exists to catch — defaulting to today, or refusing outright — made it
    pass having asserted nothing at all.
    """
    tuesday = MONDAY + timedelta(days=1)
    work(services, MONDAY, hours=9)
    work(services, tuesday, hours=9)
    services.invalidate()

    with time_machine.travel(datetime(2026, 6, 10, 11, 0, tzinfo=UTC), tick=False):
        result = services.zero_balance()

        assert result.success, result.message
        assert result.adjustment is not None
        assert result.adjustment.date == tuesday
        assert result.adjustment.date == wallclock.today() - timedelta(days=1)


def test_zeroing_twice_is_refused_the_second_time(services: Services) -> None:
    """It says so rather than writing a row that does nothing."""
    work(services, MONDAY, hours=2)
    assert services.zero_balance(MONDAY).success

    again = services.zero_balance(MONDAY)
    assert not again.success
    assert "already zero" in again.message


def test_zeroing_records_why(services: Services) -> None:
    """A year from now the row has to explain itself."""
    work(services, MONDAY, hours=2)
    result = services.zero_balance(MONDAY)
    assert result.adjustment is not None
    assert result.adjustment.reason == OPENING_BALANCE


def test_zeroing_without_a_reason_writes_nothing(services: Services) -> None:
    """The refusal has to survive the extra layer.

    `zero_balance` computes the correction and hands it to `record`, which turns
    a blank reason down. If the registry took the refusal for a success it would
    drop the memoised ledger — reporting a settled balance that was never
    written, until the next launch recomputed it and put the deficit back.
    """
    work(services, MONDAY, hours=2)

    result = services.zero_balance(MONDAY, reason="   ")

    assert not result.success
    assert "reason" in result.message
    assert services.adjustments.all() == []
    assert services.ledger.balance(MONDAY).delta != timedelta()


def test_the_records_survive_it(services: Services) -> None:
    """Settling never deletes the evidence of what actually happened."""
    work(services, MONDAY, hours=2)
    services.zero_balance(MONDAY)
    services.invalidate()
    assert len(sessions_on(services.session, MONDAY)) == 1
    assert services.ledger.day(MONDAY).worked == timedelta(hours=2)
