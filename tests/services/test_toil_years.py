"""A TOIL reservation belongs to the leave year that will pay for it."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
import time_machine

from flexi.cli.leave import run
from flexi.constants import AbsenceType, Portion, Verdict
from flexi.services.absence import PLAN_CHANGED, ToilBalance
from flexi.services.registry import Services
from tests.services.conftest import Configured

TODAY = date(2026, 10, 16)
LAST = date(2026, 10, 19)
FIRST = date(2026, 10, 20)
LATER = date(2026, 10, 21)


@pytest.fixture
def before_reset() -> Iterator[None]:
    with time_machine.travel(datetime(2026, 10, 16, 12, tzinfo=UTC), tick=False):
        yield


@pytest.fixture
def tracked(configure: Configured, before_reset: None) -> Services:
    return configure(leave_year_start="10-20", tracking_since=TODAY)


@pytest.mark.parametrize("available", [-10.0, 10.0])
def test_next_year_inherits_neither_surplus_nor_deficit(
    tracked: Services, available: float
) -> None:
    plan = tracked.absence.plan(
        FIRST, FIRST, AbsenceType.FLEXI, available_toil_days=available
    )
    assert plan.toil_balances == (ToilBalance(2026, 0.0, -1.0),)
    assert plan.toil_after == -1.0
    assert plan.warning == "This takes the flexi balance 1 day into deficit"
    result = tracked.absence.book_plan(plan)
    assert result.success
    assert result.warning == plan.warning
    assert tracked.ledger.balance(FIRST).delta == -tracked.settings.get_contracted()


def test_direct_booking_also_uses_the_booked_year(tracked: Services) -> None:
    result = tracked.absence.book(
        FIRST, AbsenceType.FLEXI, Portion.AM, available_toil_days=10.0
    )
    assert result.success
    assert (
        result.warning
        == "Booked, but this takes the flexi balance 0.5 days into deficit"
    )


def test_future_year_reserves_its_existing_valid_bookings(tracked: Services) -> None:
    assert tracked.absence.book(LATER, AbsenceType.FLEXI, Portion.AM).success
    plan = tracked.absence.plan(
        FIRST, FIRST, AbsenceType.FLEXI, available_toil_days=10.0
    )
    assert plan.toil_balances == (ToilBalance(2026, -0.5, -1.5),)
    assert plan.warning == "This takes the flexi balance 1.5 days into deficit"


def test_mixed_year_half_days_do_not_net_one_year_against_another(
    tracked: Services,
) -> None:
    assert tracked.absence.book(LATER, AbsenceType.FLEXI, Portion.AM).success
    plan = tracked.absence.plan(
        LAST, FIRST, AbsenceType.FLEXI, Portion.AM, available_toil_days=3.0
    )
    assert plan.toil_balances == (
        ToilBalance(2025, 3.0, 2.5),
        ToilBalance(2026, -0.5, -1.0),
    )
    assert plan.toil_after is None
    assert (
        plan.warning
        == "This takes the flexi balance 1 day into deficit (2026 leave year)"
    )
    result = tracked.absence.book_plan(plan)
    assert result.booked == (LAST, FIRST)
    assert result.warning == plan.warning


def test_each_overdrawn_year_gets_its_own_warning(tracked: Services) -> None:
    plan = tracked.absence.plan(LAST, FIRST, AbsenceType.FLEXI, available_toil_days=0.5)
    assert plan.warning == (
        "This takes the flexi balance 0.5 days into deficit (2025 leave year)\n"
        "This takes the flexi balance 1 day into deficit (2026 leave year)"
    )


def test_refused_days_in_next_year_cannot_consume_current_year_toil(
    tracked: Services,
) -> None:
    assert tracked.absence.book(FIRST, AbsenceType.SICK).success
    plan = tracked.absence.plan(LAST, FIRST, AbsenceType.FLEXI, available_toil_days=1.0)
    assert [day.verdict for day in plan.days] == [Verdict.BOOK, Verdict.CLASH]
    assert plan.toil_balances == (ToilBalance(2025, 1.0, 0.0),)
    assert plan.warning is None
    assert tracked.absence.book_plan(plan).booked == (LAST,)


def test_current_year_reservations_are_not_subtracted_twice(tracked: Services) -> None:
    assert tracked.absence.book(LAST, AbsenceType.FLEXI, Portion.AM).success
    contracted = tracked.settings.get_contracted()
    assert tracked.adjustments.record(TODAY, contracted * 3, "opening balance").success
    available = tracked.wallet.available_toil_days(TODAY)
    assert available == 1.5
    plan = tracked.absence.plan(
        LAST, LAST, AbsenceType.FLEXI, Portion.PM, available_toil_days=available
    )
    assert plan.toil_balances == (ToilBalance(2025, 1.5, 1.0),)
    assert plan.warning is None


def test_historical_year_cannot_spend_or_warn_about_todays_bank(
    tracked: Services,
) -> None:
    historical = date(2025, 10, 17)
    plan = tracked.absence.plan(
        historical, historical, AbsenceType.FLEXI, available_toil_days=-10.0
    )
    assert plan.toil_balances == (ToilBalance(2024, 0.0, 0.0),)
    assert plan.warning is None


def test_untracked_future_day_does_not_warn_about_existing_reservations(
    configure: Configured, before_reset: None
) -> None:
    services = configure(leave_year_start="10-20", tracking_since=LATER)
    assert services.absence.book(LATER, AbsenceType.FLEXI).success
    plan = services.absence.plan(
        FIRST, FIRST, AbsenceType.FLEXI, available_toil_days=10.0
    )
    assert plan.toil_cost == 0.0
    assert plan.toil_balances == (ToilBalance(2026, -1.0, -1.0),)
    assert plan.warning is None


def test_future_reservations_before_tracking_do_not_spend_the_new_year(
    configure: Configured, before_reset: None
) -> None:
    services = configure(leave_year_start="10-20", tracking_since=LATER)
    assert services.absence.book(FIRST, AbsenceType.FLEXI).success
    plan = services.absence.plan(
        LATER, LATER, AbsenceType.FLEXI, available_toil_days=10.0
    )
    assert plan.toil_balances == (ToilBalance(2026, 0.0, -1.0),)
    assert plan.warning == "This takes the flexi balance 1 day into deficit"
    assert services.absence.book_plan(plan).success
    assert services.ledger.balance(LATER).delta == -services.settings.get_contracted()


def test_new_future_reservation_invalidates_a_confirmed_preview(
    tracked: Services,
) -> None:
    plan = tracked.absence.plan(
        FIRST, FIRST, AbsenceType.FLEXI, available_toil_days=10.0
    )
    assert tracked.absence.book(LATER, AbsenceType.FLEXI).success
    result = tracked.absence.book_plan(plan)
    assert not result.success
    assert result.skipped == ((FIRST, PLAN_CHANGED),)
    assert tracked.absence.in_range(FIRST, FIRST) == []


def test_cli_warns_when_the_actual_next_year_balance_is_negative(
    tracked: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    contracted = tracked.settings.get_contracted()
    assert tracked.adjustments.record(TODAY, contracted * 11, "opening balance").success
    assert tracked.wallet.available_toil_days(TODAY) == 10.0
    assert (
        run(
            tracked,
            ("toil", FIRST.isoformat()),
            note=None,
            assume_yes=True,
            dry_run=False,
            today=TODAY,
        )
        == 0
    )
    assert "1 day into deficit" in capsys.readouterr().out
    assert tracked.ledger.balance(FIRST).delta == -contracted
