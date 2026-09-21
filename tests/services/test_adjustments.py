"""Settling a balance without deleting the records that made it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import time_machine
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.models.database.db import BalanceAdjustment
from flexi.models.database.engine import get_session
from flexi.services.adjustments import OPENING_BALANCE
from flexi.services.registry import (
    Services,
    build_services,
    invalidate_services,
    zero_balance,
)
from tests.conftest import sessions_on
from tests.services.conftest import CONTRACTED, Configured, work

MONDAY = date(2026, 6, 8)
FRIDAY = date(2026, 6, 12)
NEW_YEAR = ((date(2026, 1, 1), "New Year's Day"),)
"""A holiday well away from the test week, so the calendar answers at all."""


@pytest.fixture
def services(configure: Configured) -> Services:
    """A leave year that starts on the Monday of the test week."""
    return configure(
        leave_year_start="06-08", holidays=((date(2026, 1, 1), "New Year's Day"),)
    )


# the arithmetic


def test_adjustment_moves_the_balance(services: Services) -> None:
    work(services, MONDAY, hours=7.4)
    services.adjustments.record(MONDAY, timedelta(hours=3), "carried over")
    invalidate_services(services)
    assert services.ledger.balance(MONDAY).delta == timedelta(hours=3)


def test_committed_adjustment_invalidates_the_cache(
    services: Services,
) -> None:
    """A caller cannot keep reading a derivation taken before the write."""
    before = services.ledger.balance(MONDAY).delta

    services.adjustments.record(MONDAY, timedelta(hours=3), "carried over")

    assert services.ledger.balance(MONDAY).delta == before + timedelta(hours=3)


def test_adjustment_counts_from_its_own_date(services: Services) -> None:
    """A correction dated Friday does not move Monday's balance."""
    services.adjustments.record(FRIDAY, timedelta(hours=5), "carried over")
    invalidate_services(services)
    assert services.ledger.balance(MONDAY).adjustment == timedelta()
    assert services.ledger.balance(FRIDAY).adjustment == timedelta(hours=5)


def test_summary_reports_adjustments_separately(services: Services) -> None:
    services.adjustments.record(MONDAY, timedelta(hours=2), "carried over")
    invalidate_services(services)
    summary = services.ledger.summary(MONDAY, FRIDAY)
    assert summary.adjustment == timedelta(hours=2)
    assert summary.worked == timedelta()


def test_adjustments_add_up(services: Services) -> None:
    """Two corrections on one day are one correction."""
    services.adjustments.record(MONDAY, timedelta(hours=2), "carried over")
    services.adjustments.record(MONDAY, timedelta(hours=-1), "and back again")
    invalidate_services(services)
    assert services.ledger.balance(MONDAY).adjustment == timedelta(hours=1)


# the refusals


def test_adjustment_needs_a_reason(services: Services) -> None:
    result = services.adjustments.record(MONDAY, timedelta(hours=1), "   ")
    assert not result.success
    assert "reason" in result.message


@pytest.mark.parametrize(
    ("seconds", "minutes"),
    [(20, None), (29, None), (30, None), (31, 1), (40, 1), (-40, -1), (90, 2)],
)
def test_correction_lands_on_the_nearest_minute(
    services: Services, seconds: int, minutes: int | None
) -> None:
    """Nearest minute, not truncated, and zero minutes is a refusal.

    Thirty seconds is refused and ninety is two minutes, because Python rounds
    a half to even; those two rows are what tell rounding from truncation.
    """
    result = services.adjustments.record(MONDAY, timedelta(seconds=seconds), "rounding")

    assert result.success is (minutes is not None)
    if minutes is None:
        assert "zero minutes" in result.message
    else:
        assert result.adjustment is not None
        assert result.adjustment.minutes == minutes


def test_removing_an_unknown_id_says_so(services: Services) -> None:
    """The command line takes an id typed by hand, so it takes wrong ones too."""
    result = services.adjustments.remove(404)
    assert not result.success
    assert result.message == "No such adjustment"


def test_removing_one_puts_the_balance_back(services: Services) -> None:
    recorded = services.adjustments.record(MONDAY, timedelta(hours=4), "carried over")
    assert recorded.adjustment is not None
    invalidate_services(services)
    assert services.ledger.balance(MONDAY).adjustment == timedelta(hours=4)

    services.adjustments.remove(recorded.adjustment.id)
    invalidate_services(services)
    assert services.ledger.balance(MONDAY).adjustment == timedelta()


def test_removal_reserves_an_adjustment_before_reading_it(
    services: Services,
    session: Session,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent replacement cannot reuse the identity being removed."""
    recorded = services.adjustments.record(
        MONDAY, timedelta(hours=4), "original correction"
    )
    assert recorded.adjustment is not None
    adjustment_id = recorded.adjustment.id
    get = session.get
    writer_was_blocked = False

    with get_session(engine) as competing:
        competing.connection().exec_driver_sql("PRAGMA busy_timeout=0")

        def interleave(
            model: type[BalanceAdjustment], ident: int
        ) -> BalanceAdjustment | None:
            nonlocal writer_was_blocked
            row = get(model, ident)
            assert row is not None
            current = competing.get(BalanceAdjustment, ident)
            assert current is not None
            try:
                competing.delete(current)
                competing.commit()
                competing.add(
                    BalanceAdjustment(
                        date=FRIDAY,
                        minutes=30,
                        reason="replacement correction",
                        created_at=datetime(2026, 6, 12, 12),
                    )
                )
                competing.commit()
            except OperationalError:
                competing.rollback()
                writer_was_blocked = True
            return row

        monkeypatch.setattr(session, "get", interleave)
        result = services.adjustments.remove(adjustment_id)

    assert writer_was_blocked
    assert result.success
    assert services.adjustments.all() == []


# reading them back


def test_corrections_are_listed_newest_first(
    services: Services,
) -> None:
    """`flexi balance log` prints this list in the order it comes back."""
    services.adjustments.record(MONDAY, timedelta(hours=2), "carried over")
    services.adjustments.record(FRIDAY, timedelta(hours=-1), "and back again")

    assert [row.date for row in services.adjustments.all()] == [FRIDAY, MONDAY]


# zeroing


def test_zeroing_settles_the_balance_to_a_date(services: Services) -> None:
    work(services, MONDAY, hours=2)  # a short day: 2h worked against 7h24
    invalidate_services(services)
    assert services.ledger.balance(MONDAY).delta != timedelta()

    result = zero_balance(services, MONDAY)
    assert result.success
    assert services.ledger.balance(MONDAY).delta == timedelta()


def test_zeroing_leaves_the_next_day_behaving_normally(services: Services) -> None:
    """Settling draws a line under the past; it does not change the counting."""
    work(services, MONDAY, hours=2)
    zero_balance(services, MONDAY)
    invalidate_services(services)

    tuesday = MONDAY + timedelta(days=1)
    work(services, tuesday, hours=9.4)
    invalidate_services(services)
    assert services.ledger.balance(tuesday).delta == timedelta(hours=9.4) - CONTRACTED


def test_zeroing_defaults_to_yesterday(services: Services) -> None:
    """Today is not over.

    Absorbing today's contracted hours before they are worked reads as unearned
    overtime.
    """
    tuesday = MONDAY + timedelta(days=1)
    work(services, MONDAY, hours=9)
    work(services, tuesday, hours=9)
    invalidate_services(services)

    with time_machine.travel(datetime(2026, 6, 10, 11, 0, tzinfo=UTC), tick=False):
        result = zero_balance(services)

        assert result.success, result.message
        assert result.adjustment is not None
        assert result.adjustment.date == tuesday
        assert result.adjustment.date == wallclock.today() - timedelta(days=1)


@pytest.mark.parametrize("ahead", [0, 1, 90])
def test_zeroing_an_unfinished_day_is_refused(services: Services, ahead: int) -> None:
    """Today included: `settlement_date`'s rule is yesterday or earlier.

    A future correction is sized against a projection where every day until then
    was worked zero hours, and the ledger hides it (`date <= end`) until its date
    arrives. The week's real hours then read as pure surplus.
    """
    work(services, MONDAY, hours=2)
    invalidate_services(services)

    with time_machine.travel(datetime(2026, 6, 10, 11, 0, tzinfo=UTC), tick=False):
        result = zero_balance(services, wallclock.today() + timedelta(days=ahead))

        assert result.success is False
        assert "has not finished" in result.message
        assert result.adjustment is None
    assert services.adjustments.all() == []


def test_zeroing_twice_is_refused_the_second_time(services: Services) -> None:
    """It says so instead of writing a row worth zero minutes."""
    work(services, MONDAY, hours=2)
    assert zero_balance(services, MONDAY).success

    again = zero_balance(services, MONDAY)
    assert not again.success
    assert "already zero" in again.message


def test_zeroing_behind_an_existing_line_is_refused(
    services: Services,
) -> None:
    """Two overlapping settlements absorb the period they share twice.

    A line is sized from the balance up to its own date, so an earlier one
    cannot see a later one and hands back a deficit already cancelled.
    """
    work(services, MONDAY, hours=2)
    assert zero_balance(services, FRIDAY).success
    invalidate_services(services)
    settled = services.ledger.balance(FRIDAY).delta

    earlier = zero_balance(services, MONDAY)

    assert not earlier.success
    assert "12 Jun" in earlier.message
    assert "balance undo" in earlier.message
    assert len(services.adjustments.all()) == 1
    invalidate_services(services)
    assert services.ledger.balance(FRIDAY).delta == settled


def test_zeroing_an_earlier_leave_year_is_allowed(services: Services) -> None:
    """Each leave year accumulates from its own start, so the two cannot overlap."""
    work(services, MONDAY, hours=2)
    assert zero_balance(services, FRIDAY).success

    previous = zero_balance(services, MONDAY - timedelta(days=3))

    assert previous.success, previous.message
    assert len(services.adjustments.all()) == 2


def test_settlement_reports_hours_and_minutes(services: Services) -> None:
    work(services, MONDAY, hours=2)

    result = zero_balance(services, MONDAY)

    assert "+5:24" in result.message


def test_zeroing_recomputes_after_an_external_commit(
    services: Services, engine: Engine
) -> None:
    """A cached preview cannot make settlement leave another writer's delta."""
    work(services, MONDAY, hours=2)
    preview = services.ledger.balance(MONDAY).delta

    with get_session(engine) as competing_session:
        competing = build_services(competing_session)
        competing.adjustments.record(
            MONDAY, timedelta(minutes=30), "external correction"
        )

    assert services.ledger.balance(MONDAY).delta == preview + timedelta(minutes=30)
    assert zero_balance(services, MONDAY).success

    with get_session(engine) as fresh_session:
        fresh = build_services(fresh_session)
        assert fresh.ledger.balance(MONDAY).delta == timedelta()


def test_zeroing_records_why(services: Services) -> None:
    work(services, MONDAY, hours=2)
    result = zero_balance(services, MONDAY)
    assert result.adjustment is not None
    assert result.adjustment.reason == OPENING_BALANCE


def test_zeroing_without_a_reason_writes_nothing(services: Services) -> None:
    """The refusal has to survive the registry.

    `zero_balance` hands the correction to `record`, which turns a blank reason
    down; taking that for a success would drop the memoised ledger.
    """
    work(services, MONDAY, hours=2)

    result = zero_balance(services, MONDAY, reason="   ")

    assert not result.success
    assert "reason" in result.message
    assert services.adjustments.all() == []
    assert services.ledger.balance(MONDAY).delta != timedelta()


def test_zeroing_keeps_the_records(services: Services, session: Session) -> None:
    work(services, MONDAY, hours=2)
    zero_balance(services, MONDAY)
    invalidate_services(services)
    assert len(sessions_on(session, MONDAY)) == 1
    assert services.ledger.day(MONDAY).worked == timedelta(hours=2)
