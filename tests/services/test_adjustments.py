"""Settling or adjusting a balance without deleting the records that made it."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
import time_machine
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.models.database.db import BalanceAdjustment
from flexi.models.database.engine import get_session
from flexi.services.adjustments import SETTLED, parse_amount
from flexi.services.registry import (
    Services,
    adjust_balance,
    build_services,
    invalidate_services,
    zero_balance,
)
from tests.conftest import sessions_on
from tests.services.conftest import CONTRACTED, Configured, work

MONDAY = date(2026, 6, 8)
TUESDAY = date(2026, 6, 9)
WEDNESDAY = date(2026, 6, 10)
FRIDAY = date(2026, 6, 12)
NEW_YEAR = ((date(2026, 1, 1), "New Year's Day"),)
"""A holiday well away from the test week, so the calendar answers at all."""

BROUGHT_FORWARD = timedelta(hours=5, minutes=30)


@pytest.fixture
def services(configure: Configured) -> Services:
    """A leave year that starts on the Monday of the test week."""
    return configure(
        leave_year_start="06-08", holidays=((date(2026, 1, 1), "New Year's Day"),)
    )


@pytest.fixture
def on_wednesday() -> Iterator[None]:
    """Noon on the Wednesday of the test week, two days into the leave year."""
    with time_machine.travel(datetime(2026, 6, 10, 12, 0, tzinfo=UTC), tick=False):
        yield


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


def test_zeroing_settles_an_adjustment_dated_inside_it(services: Services) -> None:
    """Zero means zero: an opening balance before the line is settled with the rest."""
    work(services, MONDAY, hours=2)
    services.adjustments.record(MONDAY, BROUGHT_FORWARD, "Brought forward")

    assert zero_balance(services, TUESDAY).success
    assert services.ledger.balance(TUESDAY).delta == timedelta()


def test_zeroing_behind_a_manual_adjustment_is_refused(services: Services) -> None:
    """A later correction stops a settlement as a later settlement does.

    A reason is free text, `zero --reason` included, so a correction cannot be
    told from a settlement, and a line drawn behind a settlement absorbs the
    period the two share twice. The refusal names an adjustment, not a line,
    because that much is certain.
    """
    work(services, MONDAY, hours=2)
    later = services.adjustments.record(FRIDAY, timedelta(hours=1), "Missed meeting")
    assert later.adjustment is not None

    behind = zero_balance(services, MONDAY)

    assert not behind.success
    assert "An adjustment" in behind.message
    assert "Fri 12 Jun 2026" in behind.message
    assert f"flexi balance undo {later.adjustment.id}" in behind.message
    assert zero_balance(services, FRIDAY).success, "on its date it is settled too"


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
    assert result.adjustment.reason == SETTLED


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


# reading an amount


@pytest.mark.parametrize(
    ("typed", "minutes"),
    [
        ("+5:30", 330),
        ("5:30", 330),
        ("-1:30", -90),
        ("\N{MINUS SIGN}1:30", -90),
        ("+0:05", 5),
        (" 12:00 ", 720),
    ],
)
def test_amount_reads_signed_hours_and_minutes(typed: str, minutes: int) -> None:
    """No sign is a surplus. A deficit takes a hyphen or the U+2212 Flexi prints."""
    assert parse_amount(typed) == timedelta(minutes=minutes)


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "5",
        "+5",
        "5:3",
        "5:60",
        "1:30:00",
        "+-1:30",
        "--1:30",
        "+ 1:30",
        "5.30",
        "1h30",
        "10000:00",
    ],
)
def test_unreadable_amount_names_the_form_it_takes(typed: str) -> None:
    """A bare 5 is refused too: five hours or five minutes cannot be told apart."""
    with pytest.raises(ValueError, match="use H:MM"):
        parse_amount(typed)


@pytest.mark.parametrize("typed", ["0:00", "+0:00", "-0:00", "\N{MINUS SIGN}0:00"])
def test_zero_amount_is_refused(typed: str) -> None:
    with pytest.raises(ValueError, match="change nothing"):
        parse_amount(typed)


# adjusting


@pytest.mark.usefixtures("on_wednesday")
def test_adjusting_counts_from_today(services: Services) -> None:
    result = adjust_balance(services, BROUGHT_FORWARD, "Brought forward")

    assert result.success, result.message
    assert result.adjustment is not None
    assert result.adjustment.date == WEDNESDAY
    assert services.ledger.balance(WEDNESDAY).adjustment == BROUGHT_FORWARD
    assert services.ledger.balance(TUESDAY).adjustment == timedelta()


@pytest.mark.usefixtures("on_wednesday")
def test_adjustment_can_be_dated_earlier_in_the_leave_year(
    services: Services,
) -> None:
    result = adjust_balance(services, timedelta(minutes=-90), "Left early", MONDAY)

    assert result.success, result.message
    assert services.ledger.day(MONDAY).adjustment == timedelta(minutes=-90)


@pytest.mark.usefixtures("on_wednesday")
def test_adjustment_in_the_future_is_refused(services: Services) -> None:
    """The ledger would hide it until its date, and today's figure would not move."""
    result = adjust_balance(services, timedelta(hours=1), "Early", FRIDAY)

    assert not result.success
    assert "has not happened" in result.message
    assert services.adjustments.all() == []


@pytest.mark.usefixtures("on_wednesday")
def test_adjustment_before_the_leave_year_is_refused(services: Services) -> None:
    """The balance starts again each leave year, so it would never count.

    It would still be listed by `flexi balance log`, as if it did.
    """
    result = adjust_balance(services, timedelta(hours=1), "Late", date(2026, 6, 7))

    assert not result.success
    assert "Sun 7 Jun 2026" in result.message
    assert "Mon 8 Jun 2026" in result.message, "the refusal names the first day"
    assert services.adjustments.all() == []


def test_first_day_of_a_leave_year_counts_in_it(services: Services) -> None:
    """Today, never yesterday: on the first day, yesterday is the previous year."""
    with time_machine.travel(datetime(2026, 6, 8, 9, 0, tzinfo=UTC), tick=False):
        assert adjust_balance(services, BROUGHT_FORWARD, "Brought forward").success
        assert services.ledger.balance(MONDAY).adjustment == BROUGHT_FORWARD

        yesterday = adjust_balance(
            services, BROUGHT_FORWARD, "Brought forward", date(2026, 6, 7)
        )
        assert not yesterday.success


@pytest.mark.usefixtures("on_wednesday")
def test_adjustment_behind_a_settlement_is_refused(services: Services) -> None:
    """On or before its date, a row would reopen what the settlement closed."""
    work(services, MONDAY, hours=2)
    line = zero_balance(services, TUESDAY)
    assert line.adjustment is not None

    for when in (MONDAY, TUESDAY):
        refused = adjust_balance(services, timedelta(hours=1), "Late claim", when)

        assert not refused.success
        assert "Tue 9 Jun 2026" in refused.message
        assert f"flexi balance undo {line.adjustment.id}" in refused.message
    assert len(services.adjustments.all()) == 1
    assert services.ledger.balance(TUESDAY).delta == timedelta()


@pytest.mark.usefixtures("on_wednesday")
def test_settlement_under_any_reason_holds_its_line(services: Services) -> None:
    """`zero --reason` is free text, so every row before today is a possible line.

    The refusal says adjustment, not settlement: the row in the way may be a
    correction recorded a day late.
    """
    work(services, MONDAY, hours=2)
    assert zero_balance(services, MONDAY, reason="Agreed with my manager").success
    services.adjustments.record(TUESDAY, timedelta(minutes=15), "Missed meeting")

    refused = adjust_balance(services, timedelta(hours=1), "Late claim", MONDAY)

    assert not refused.success
    assert "An adjustment is already recorded on Tue 9 Jun 2026" in refused.message, (
        "the latest line, not the first"
    )


@pytest.mark.usefixtures("on_wednesday")
def test_adjustment_after_a_settlement_counts(services: Services) -> None:
    work(services, MONDAY, hours=2)
    assert zero_balance(services, MONDAY).success

    result = adjust_balance(services, timedelta(hours=1), "Late claim", TUESDAY)

    assert result.success, result.message
    assert services.ledger.day(TUESDAY).adjustment == timedelta(hours=1)


@pytest.mark.usefixtures("on_wednesday")
def test_adjustments_can_share_today(services: Services) -> None:
    """A row dated today is never a settlement: only a finished day is settled."""
    assert adjust_balance(services, BROUGHT_FORWARD, "Brought forward").success
    assert adjust_balance(services, timedelta(minutes=-30), "Long lunch").success

    assert services.ledger.balance(WEDNESDAY).adjustment == timedelta(hours=5)


@pytest.mark.usefixtures("on_wednesday")
def test_adjustment_is_refused_with_no_reason(services: Services) -> None:
    result = adjust_balance(services, BROUGHT_FORWARD, "  ")

    assert not result.success
    assert "reason" in result.message
    assert services.adjustments.all() == []
