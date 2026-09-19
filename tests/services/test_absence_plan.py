"""Deciding a booking without making it.

`plan` writes nothing, accounts for every calendar date in the span, and gives
each day a typed verdict, so the confirmation prompt built from it is a
question and not a receipt.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time

import pytest
import time_machine
from sqlalchemy import delete
from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Portion, Verdict
from flexi.models.database.db import AbsenceDay, BankHolidayRefresh
from flexi.services.absence import PLAN_CHANGED
from flexi.services.registry import Services, invalidate_services
from tests.services.conftest import Configured, work

MONDAY = date(2026, 8, 10)
FRIDAY = date(2026, 8, 14)
SATURDAY = date(2026, 8, 15)
SUNDAY = date(2026, 8, 16)
BANK_HOLIDAY = date(2026, 8, 31)
SEPT_FRIDAY = date(2026, 9, 4)
BEFORE_THE_SPAN = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
"""The Friday before MONDAY.

TOIL only draws on the flexi balance for days the balance has not already
counted, so the span has to be ahead of today for the overdraw warning to have
anything to warn about.
"""
MID_SPAN = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
"""The Wednesday inside it."""
AFTER_THE_SPAN = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
"""The Thursday after it, by which time every day in it has been counted."""


@pytest.fixture
def before_the_span() -> Iterator[None]:
    with time_machine.travel(BEFORE_THE_SPAN, tick=False):
        yield


def _configure(
    configure: Configured, *, holidays: bool = True, days: float = 25.0
) -> Services:
    """The shared fixture, with the two knobs this file turns."""
    return configure(
        entitlement=(2025, days),
        holidays=((BANK_HOLIDAY, "Summer bank holiday"),) if holidays else (),
    )


def _rows(session: Session) -> int:
    return session.query(AbsenceDay).count()


# ---------- planning writes nothing ----------


def test_planning_writes_nothing(services: Services, session: Session) -> None:
    services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert _rows(session) == 0


def test_planning_twice_gives_the_same_answer(services: Services) -> None:
    first = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    second = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert [d.verdict for d in first.days] == [d.verdict for d in second.days]
    assert first.cost == second.cost


# ---------- weekends and bank holidays ----------


def test_weekend_is_in_the_plan(services: Services) -> None:
    plan = services.absence.plan(MONDAY, SUNDAY, AbsenceType.ANNUAL)

    assert len(plan.days) == 7, "every calendar date is accounted for"
    weekend = {d.date: d.verdict for d in plan.days if d.date in {SATURDAY, SUNDAY}}
    assert weekend == {SATURDAY: Verdict.NON_WORKING, SUNDAY: Verdict.NON_WORKING}
    assert {d.date for d in plan.skipped} == {SATURDAY, SUNDAY}


def test_weekend_is_not_a_refusal(services: Services) -> None:
    """Counting Saturdays as failures makes every fortnight look partial."""
    plan = services.absence.plan(MONDAY, SUNDAY, AbsenceType.ANNUAL)
    assert plan.refused == ()
    assert len(plan.bookable) == 5


def test_bank_holiday_is_typed_not_pattern_matched(services: Services) -> None:
    plan = services.absence.plan(BANK_HOLIDAY, BANK_HOLIDAY, AbsenceType.ANNUAL)
    day = plan.days[0]
    assert day.verdict is Verdict.BANK_HOLIDAY
    assert day.detail == "Summer bank holiday", "the plan can name it"
    assert plan.refused == ()


def test_missing_calendar_data_is_a_refusal_not_a_skip(
    configure: Configured, session: Session
) -> None:
    """An unavailable calendar leaves the day's bookability unknown.

    Passing over it silently would lose the day without saying so.
    """
    services = _configure(configure, holidays=False)
    session.execute(delete(BankHolidayRefresh))
    session.commit()
    plan = services.absence.plan(MONDAY, MONDAY, AbsenceType.ANNUAL)

    assert plan.days[0].verdict is Verdict.NO_CALENDAR
    assert plan.refused, "it is refused, not skipped"
    assert plan.skipped == ()


# ---------- the entitlement across the plan ----------


def test_allowance_is_drawn_down_across_the_plan(configure: Configured) -> None:
    """Three days left and five asked for, so the last two are refused.

    Reading the database fresh for each day would approve all five.
    """
    services = _configure(configure, days=3.0)
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    assert len(plan.bookable) == 3
    assert len(plan.refused) == 2
    assert all(d.verdict is Verdict.NO_ENTITLEMENT for d in plan.refused)


def test_plan_says_what_it_would_cost(services: Services) -> None:
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert plan.cost == 5.0
    assert plan.annual_remaining == 25.0
    assert plan.annual_after == 20.0


def test_each_leave_year_spends_only_its_own_entitlement(
    configure: Configured,
) -> None:
    """A range crossing New Year carries two independent allowances."""
    services = configure(
        leave_year_start="01-01",
        holidays=((BANK_HOLIDAY, "Summer bank holiday"),),
    )
    services.settings.save_entitlement(2026, 1.0)
    services.settings.save_entitlement(2027, 2.0)

    plan = services.absence.plan(
        date(2026, 12, 30), date(2027, 1, 5), AbsenceType.ANNUAL
    )

    assert [
        (balance.year, balance.before, balance.after)
        for balance in plan.annual_balances
    ] == [
        (2026, 1.0, 0.0),
        (2027, 2.0, 0.0),
    ]
    assert [day.date for day in plan.bookable] == [
        date(2026, 12, 30),
        date(2027, 1, 1),
        date(2027, 1, 4),
    ]
    assert [day.date for day in plan.refused] == [
        date(2026, 12, 31),
        date(2027, 1, 5),
    ]


def test_half_days_cost_a_half(services: Services) -> None:
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL, Portion.AM)
    assert plan.cost == 2.5
    assert plan.annual_after == 22.5


def test_sick_leave_does_not_touch_the_annual_allowance(services: Services) -> None:
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.SICK)
    assert plan.annual_after == plan.annual_remaining


# ---------- executing a plan ----------


def test_booking_a_plan_writes_exactly_what_it_said(
    services: Services, session: Session
) -> None:
    plan = services.absence.plan(MONDAY, SUNDAY, AbsenceType.ANNUAL)
    result = services.absence.book_plan(plan)

    assert _rows(session) == 5
    assert set(result.booked) == {d.date for d in plan.bookable}
    booked = {row.date for row in session.query(AbsenceDay).all()}
    assert SATURDAY not in booked
    assert SUNDAY not in booked


def test_plan_with_nothing_to_do_writes_nothing(
    services: Services, session: Session
) -> None:
    plan = services.absence.plan(SATURDAY, SUNDAY, AbsenceType.ANNUAL)
    assert plan.is_empty
    result = services.absence.book_plan(plan)
    assert _rows(session) == 0
    assert not result.success


def test_book_range_plans_and_then_executes(
    services: Services, session: Session
) -> None:
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 5
    assert not result.skipped
    assert _rows(session) == 5


def test_work_recorded_after_a_preview_refuses_it(
    services: Services, session: Session
) -> None:
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    work(services, MONDAY, 8)

    result = services.absence.book_plan(plan)

    assert result.booked == ()
    assert result.skipped == ((MONDAY, PLAN_CHANGED),)
    assert _rows(session) == 0


def test_entitlement_changed_after_a_preview_refuses_it(
    services: Services, session: Session
) -> None:
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    services.settings.save_entitlement(2025, 0.0)

    result = services.absence.book_plan(plan)

    assert result.booked == ()
    assert result.skipped == ((MONDAY, PLAN_CHANGED),)
    assert _rows(session) == 0


def test_span_across_a_bank_holiday_books_the_rest(
    services: Services, session: Session
) -> None:
    plan = services.absence.plan(BANK_HOLIDAY, SEPT_FRIDAY, AbsenceType.ANNUAL)
    assert len(plan.bookable) == 4
    assert len(plan.skipped) == 1
    services.absence.book_plan(plan)
    assert _rows(session) == 4


# ---------- the flexi balance ----------


def test_overdrawn_balance_only_warns(
    services: Services, before_the_span: None
) -> None:
    """A flexi balance is your own arithmetic; going under is a decision."""
    plan = services.absence.plan(
        MONDAY, FRIDAY, AbsenceType.FLEXI, available_toil_days=2.0
    )
    assert len(plan.bookable) == 5, "not refused"
    assert plan.toil_after == -3.0
    assert plan.warning is not None
    assert "3 day" in plan.warning


def test_no_warning_when_the_balance_covers_it(
    services: Services, before_the_span: None
) -> None:
    plan = services.absence.plan(
        MONDAY, FRIDAY, AbsenceType.FLEXI, available_toil_days=10.0
    )
    assert plan.warning is None
    assert plan.toil_after == 5.0


def test_annual_leave_leaves_the_toil_balance_alone(
    services: Services,
) -> None:
    """The confirmation shows both figures, and only one of them moves."""
    plan = services.absence.plan(
        MONDAY, FRIDAY, AbsenceType.ANNUAL, available_toil_days=2.0
    )

    assert plan.toil_after == 2.0
    assert plan.warning is None, "an overdrawn balance is not this booking's news"


def test_toil_on_a_past_day_leaves_the_balance_alone(
    services: Services,
) -> None:
    """Relabelling a shortfall is not a withdrawal.

    The day already scored the shortfall for its unworked contracted hours, so
    booking TOIL over it trades one for the other.
    """
    with time_machine.travel(AFTER_THE_SPAN, tick=False):
        plan = services.absence.plan(
            MONDAY, MONDAY, AbsenceType.FLEXI, available_toil_days=0.0
        )

        assert plan.toil_after == 0.0
        assert plan.warning is None

        before = services.wallet.available_toil_days()
        assert services.absence.book_plan(plan).success
        invalidate_services(services)

        assert services.wallet.available_toil_days() == before


def test_span_charges_only_the_days_still_to_come(
    services: Services,
) -> None:
    """Wednesday's preview of the whole week is about Thursday and Friday."""
    with time_machine.travel(MID_SPAN, tick=False):
        plan = services.absence.plan(
            MONDAY, FRIDAY, AbsenceType.FLEXI, available_toil_days=2.0
        )

    assert len(plan.bookable) == 5
    assert plan.toil_after == 0.0
    assert plan.warning is None


def test_toil_before_tracking_began_is_a_real_withdrawal(
    configure: Configured,
) -> None:
    """A day Flexi was not watching expects nothing, so TOIL on it takes hours.

    Hours recorded after the fact are a memory of the day, not proof Flexi was
    there for it, and that is the distinction the ledger draws.
    """
    services = configure(entitlement=(2025, 25.0), tracking_since=FRIDAY)
    assert services.clock.correct(MONDAY, time(13, 0), time(17, 0)).success

    plan = services.absence.plan(
        MONDAY, MONDAY, AbsenceType.FLEXI, Portion.AM, available_toil_days=0.0
    )

    assert len(plan.bookable) == 1
    assert plan.toil_after == -0.5
    assert plan.warning is not None


def test_punched_day_before_tracking_is_counted(
    configure: Configured,
) -> None:
    """Something clocked in, so the ledger expects that day's hours of it."""
    services = configure(entitlement=(2025, 25.0), tracking_since=FRIDAY)
    work(services, MONDAY, hours=4, start_hour=13)

    plan = services.absence.plan(
        MONDAY, MONDAY, AbsenceType.FLEXI, Portion.AM, available_toil_days=0.0
    )

    assert len(plan.bookable) == 1
    assert plan.toil_after == 0.0
    assert plan.warning is None


def test_refused_day_says_what_is_left(
    configure: Configured,
) -> None:
    """The shortfall on one day is at most one day, whatever the request."""
    services = _configure(configure, days=0.5)

    plan = services.absence.plan(MONDAY, MONDAY, AbsenceType.ANNUAL)

    assert plan.reasons == ("Not enough annual leave — only 0.5 days left",)


def test_plan_names_each_refusal_once(services: Services) -> None:
    """The dialog lists them, so a reason repeated per day crowds out the rest."""
    services.absence.book(MONDAY, AbsenceType.SICK)
    services.settings.save_entitlement(2025, 0.0)

    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    assert len(plan.refused) == 5
    assert plan.reasons == (
        "That day is already booked in full",
        "Not enough annual leave left",
    )
