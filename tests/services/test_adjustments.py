"""Settling a balance without deleting the records that made it."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base, BankHolidayCache
from flexi.services.adjustments import OPENING_BALANCE
from flexi.services.registry import Services

MONDAY = date(2026, 6, 8)
FRIDAY = date(2026, 6, 12)
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
    """A leave year that starts on the Monday of the test week."""
    built = Services.build(session)
    built.settings.save_settings(
        leave_year_start="06-08",
        working_days="0,1,2,3,4",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
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
    start = datetime.combine(when, datetime.min.time(), tzinfo=timezone.utc).replace(hour=9)
    services.clock.clock_in(now=start)
    services.clock.clock_out(now=start + timedelta(hours=hours))
    services.invalidate()


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


def test_removing_one_puts_the_balance_back(services: Services) -> None:
    """One row in, one row out."""
    recorded = services.adjustments.record(MONDAY, timedelta(hours=4), "carried over")
    assert recorded.adjustment is not None
    services.invalidate()
    assert services.ledger.balance(MONDAY).adjustment == timedelta(hours=4)

    services.adjustments.remove(recorded.adjustment.id)
    services.invalidate()
    assert services.ledger.balance(MONDAY).adjustment == timedelta()


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
    """
    result = services.zero_balance()
    if result.success:
        assert result.adjustment is not None
        assert result.adjustment.date == date.today() - timedelta(days=1)


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


def test_the_records_survive_it(services: Services) -> None:
    """Settling never deletes the evidence of what actually happened."""
    work(services, MONDAY, hours=2)
    services.zero_balance(MONDAY)
    services.invalidate()
    assert len(services.clock.get_sessions_for_date(MONDAY)) == 1
    assert services.ledger.day(MONDAY).worked == timedelta(hours=2)
