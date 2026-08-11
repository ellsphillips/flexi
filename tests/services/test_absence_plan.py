"""Deciding a booking without making it.

book_range used to call book in a loop, and book commits, so a confirmation
prompt built from its result was a receipt rather than a question. It also
erased weekends and bank holidays from the result entirely -- they reached
neither `booked` nor `skipped` -- and told a bank holiday apart from a real
refusal by looking for the words "bank holiday" in a sentence written for a
status bar, which matched "That day is already a bank holiday" and missed
"Bank holiday data unavailable; cannot book absence" on the capital B alone.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from flexi.constants import AbsenceType, Portion, Verdict
from flexi.models.database.db import AbsenceDay
from flexi.services.registry import Services
from tests.services.conftest import Configured

MONDAY = date(2026, 8, 10)
FRIDAY = date(2026, 8, 14)
SATURDAY = date(2026, 8, 15)
SUNDAY = date(2026, 8, 16)
BANK_HOLIDAY = date(2026, 8, 31)
SEPT_FRIDAY = date(2026, 9, 4)


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


# -- planning writes nothing ------------------------------------------------


def test_planning_writes_nothing(services: Services, session: Session) -> None:
    """The whole point. A prompt fed from a write is not a prompt."""
    services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert _rows(session) == 0


def test_planning_twice_gives_the_same_answer(services: Services) -> None:
    """It must not consume anything as a side effect of being asked."""
    first = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    second = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert [d.verdict for d in first.days] == [d.verdict for d in second.days]
    assert first.cost == second.cost


# -- the days that used to vanish -------------------------------------------


def test_a_weekend_is_in_the_plan_rather_than_erased(services: Services) -> None:
    plan = services.absence.plan(MONDAY, SUNDAY, AbsenceType.ANNUAL)

    assert len(plan.days) == 7, "every calendar date is accounted for"
    weekend = {d.date: d.verdict for d in plan.days if d.date in {SATURDAY, SUNDAY}}
    assert weekend == {SATURDAY: Verdict.NON_WORKING, SUNDAY: Verdict.NON_WORKING}
    assert {d.date for d in plan.skipped} == {SATURDAY, SUNDAY}


def test_a_weekend_is_not_a_refusal(services: Services) -> None:
    """Counting Saturdays as failures makes every fortnight look partial."""
    plan = services.absence.plan(MONDAY, SUNDAY, AbsenceType.ANNUAL)
    assert plan.refused == ()
    assert len(plan.bookable) == 5


def test_a_bank_holiday_is_typed_not_pattern_matched(services: Services) -> None:
    plan = services.absence.plan(BANK_HOLIDAY, BANK_HOLIDAY, AbsenceType.ANNUAL)
    day = plan.days[0]
    assert day.verdict is Verdict.BANK_HOLIDAY
    assert day.detail == "Summer bank holiday", "the plan can name it"
    assert plan.refused == ()


def test_missing_calendar_data_is_a_refusal_not_a_skip(configure: Configured) -> None:
    """The case the substring match missed, on a capital B.

    "Bank holiday data unavailable" means we do not know whether the day is
    bookable. Passing over it silently would lose the day without saying so.
    """
    services = _configure(configure, holidays=False)
    plan = services.absence.plan(MONDAY, MONDAY, AbsenceType.ANNUAL)

    assert plan.days[0].verdict is Verdict.NO_CALENDAR
    assert plan.refused, "it is refused, not skipped"
    assert plan.skipped == ()


# -- the entitlement is simulated across the plan ---------------------------


def test_the_allowance_is_drawn_down_across_the_plan(configure: Configured) -> None:
    """Three days left, five asked for: the last two must be refused.

    Reading the database fresh for each day would approve all five, because
    nothing has been written yet.
    """
    services = _configure(configure, days=3.0)
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    assert len(plan.bookable) == 3
    assert len(plan.refused) == 2
    assert all(d.verdict is Verdict.NO_ENTITLEMENT for d in plan.refused)


def test_the_plan_says_what_it_would_cost(services: Services) -> None:
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert plan.cost == 5.0
    assert plan.annual_remaining == 25.0
    assert plan.annual_after == 20.0


def test_half_days_cost_a_half(services: Services) -> None:
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.ANNUAL, Portion.AM)
    assert plan.cost == 2.5
    assert plan.annual_after == 22.5


def test_sick_leave_does_not_touch_the_annual_allowance(services: Services) -> None:
    plan = services.absence.plan(MONDAY, FRIDAY, AbsenceType.SICK)
    assert plan.annual_after == plan.annual_remaining


# -- executing a plan --------------------------------------------------------


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


def test_a_plan_with_nothing_to_do_writes_nothing(
    services: Services, session: Session
) -> None:
    plan = services.absence.plan(SATURDAY, SUNDAY, AbsenceType.ANNUAL)
    assert plan.is_empty
    result = services.absence.book_plan(plan)
    assert _rows(session) == 0
    assert not result.success


def test_book_range_still_behaves_as_it_did(
    services: Services, session: Session
) -> None:
    """The old entry point is now plan + execute, and must not have moved."""
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 5
    assert not result.skipped
    assert _rows(session) == 5


def test_a_span_across_a_bank_holiday_books_the_rest(
    services: Services, session: Session
) -> None:
    plan = services.absence.plan(BANK_HOLIDAY, SEPT_FRIDAY, AbsenceType.ANNUAL)
    assert len(plan.bookable) == 4
    assert len(plan.skipped) == 1
    services.absence.book_plan(plan)
    assert _rows(session) == 4


# -- the flexi balance -------------------------------------------------------


def test_an_overdrawn_balance_warns_rather_than_refuses(services: Services) -> None:
    """A flexi balance is your own arithmetic; going under is a decision."""
    plan = services.absence.plan(
        MONDAY, FRIDAY, AbsenceType.FLEXI, available_toil_days=2.0
    )
    assert len(plan.bookable) == 5, "not refused"
    assert plan.toil_after == -3.0
    assert plan.warning is not None
    assert "3 day" in plan.warning


def test_no_warning_when_the_balance_covers_it(services: Services) -> None:
    plan = services.absence.plan(
        MONDAY, FRIDAY, AbsenceType.FLEXI, available_toil_days=10.0
    )
    assert plan.warning is None
    assert plan.toil_after == 5.0
