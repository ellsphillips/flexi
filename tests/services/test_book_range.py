"""Booking a span: partial where it has to be, and explicit about skips."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import time_machine

from flexi.constants import AbsenceType, Portion
from flexi.services.absence import PLAN_CHANGED, RemovalBooking
from flexi.services.registry import Services
from tests.services.conftest import DEFAULT_HOLIDAY

MONDAY = date(2026, 8, 10)
FRIDAY = date(2026, 8, 14)
NEXT_FRIDAY = date(2026, 8, 21)
BANK_HOLIDAY = DEFAULT_HOLIDAY
BEFORE_THE_SPAN = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
"""The Friday before MONDAY; TOIL draws only on days the balance has not counted."""


def test_working_week_books_five_days(services: Services) -> None:
    """Monday to Friday is five, not seven."""
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 5
    assert not result.skipped


def test_weekends_are_skipped_quietly(services: Services) -> None:
    """Reporting the Saturdays as refusals would bury the refusal that matters."""
    result = services.absence.book_range(MONDAY, NEXT_FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 10
    assert not result.skipped
    assert "10 days" in result.message("booked")


def test_bank_holidays_are_skipped_quietly(services: Services) -> None:
    """A bank holiday is not bookable, and asking for one is not an error."""
    result = services.absence.book_range(
        BANK_HOLIDAY, BANK_HOLIDAY + timedelta(days=4), AbsenceType.ANNUAL
    )
    assert BANK_HOLIDAY not in result.booked
    assert len(result.booked) == 4
    assert not result.skipped


def test_day_already_booked_is_reported(services: Services) -> None:
    """An existing booking is the one skip worth reporting."""
    services.absence.book(date(2026, 8, 12), AbsenceType.SICK)
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 4
    assert [when for when, _ in result.skipped] == [date(2026, 8, 12)]
    assert "already booked" in result.message("booked")


def test_partial_booking_when_leave_runs_out(services: Services) -> None:
    """Partial, and it says how far it got."""
    services.settings.save_entitlement(2025, 2.0)
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 2
    assert len(result.skipped) == 3
    assert "Not enough annual leave" in result.message("booked")


def test_toil_across_a_range_warns_once(services: Services) -> None:
    """Once for the span, not once per day."""
    with time_machine.travel(BEFORE_THE_SPAN, tick=False):
        result = services.absence.book_range(
            MONDAY, FRIDAY, AbsenceType.FLEXI, available_toil_days=1.0
        )
    assert len(result.booked) == 5
    assert result.warning is not None
    assert "deficit" in result.warning


def test_half_days_across_a_range(services: Services) -> None:
    """Five mornings is two and a half days of leave."""
    services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL, Portion.AM)
    used = services.absence.count_days(AbsenceType.ANNUAL, MONDAY, FRIDAY)
    assert used == 2.5


def test_clearing_a_range_removes_what_is_there(services: Services) -> None:
    """It reports what it removed, not what was already free."""
    services.absence.book_range(MONDAY, date(2026, 8, 12), AbsenceType.ANNUAL)
    result = services.absence.clear_range(MONDAY, FRIDAY)
    assert len(result.booked) == 3
    assert not result.skipped
    assert services.absence.in_range(MONDAY, FRIDAY) == []


def test_clearing_an_empty_range_says_so(services: Services) -> None:
    result = services.absence.clear_range(MONDAY, FRIDAY)
    assert not result.success
    assert result.message("removed") == "Nothing to do"


def test_span_that_books_nothing_gives_the_reason(
    services: Services,
) -> None:
    """When every day is turned down for one reason, that reason is the answer."""
    services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    again = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    assert not again.success
    assert len(again.skipped) == 5
    assert again.message("booked") == "That day is already booked in full"


def test_span_refused_twice_over_names_both_reasons(
    services: Services,
) -> None:
    """Each reason once, however many days it accounts for."""
    services.absence.book(MONDAY, AbsenceType.SICK)
    services.settings.save_entitlement(2025, 0.0)

    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    assert not result.success
    assert len(result.skipped) == 5
    assert result.message("booked") == (
        "Nothing booked: That day is already booked in full; "
        "Not enough annual leave left"
    )


def test_single_day_reads_as_a_day(services: Services) -> None:
    """One day reads as "1 day booked", not "1 days booked"."""
    result = services.absence.book_range(MONDAY, MONDAY, AbsenceType.SICK)
    assert result.message("booked") == "1 day booked"


def test_week_of_afternoons_reads_as_afternoons(services: Services) -> None:
    """Five afternoons are two and a half days, not five whole days."""
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL, Portion.PM)

    assert result.message("of annual leave booked") == (
        "5 afternoons of annual leave booked"
    )


def test_clearing_afternoons_reads_as_afternoons(services: Services) -> None:
    """The removal answers in the unit the confirmation asked in."""
    services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL, Portion.PM)

    result = services.absence.clear_range(MONDAY, FRIDAY)

    assert result.message("removed") == "5 afternoons removed"


def test_clearing_both_halves_reads_as_a_day(services: Services) -> None:
    """A morning and an afternoon off one date are one day, not one morning."""
    services.absence.book(MONDAY, AbsenceType.ANNUAL, Portion.AM)
    services.absence.book(MONDAY, AbsenceType.SICK, Portion.PM)

    result = services.absence.clear_range(MONDAY, MONDAY)

    assert result.message("removed") == "1 day removed"


def test_removal_plan_names_what_would_go(services: Services) -> None:
    """Nine days of annual leave and nine sick mornings are different to agree to."""
    services.absence.book_range(MONDAY, date(2026, 8, 12), AbsenceType.ANNUAL)
    services.absence.book_range(
        date(2026, 8, 13), FRIDAY, AbsenceType.FLEXI, Portion.AM
    )

    plan = services.absence.removal_plan(MONDAY, FRIDAY)
    rows = services.absence.in_range(MONDAY, FRIDAY)

    assert plan.count == 5
    assert not plan.is_empty
    assert plan.bookings == tuple(
        RemovalBooking(
            absence_id=row.id,
            date=row.date,
            absence_type=row.absence_type,
            portion=row.portion,
            note=row.note,
        )
        for row in rows
    )
    assert plan.summary == ("  3 days of annual leave\n  2 mornings of TOIL")


def test_removal_plan_removes_nothing(services: Services) -> None:
    """Planning is the half that does not write, on this side too."""
    services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    services.absence.removal_plan(MONDAY, FRIDAY)

    assert len(services.absence.in_range(MONDAY, FRIDAY)) == 5


def test_removal_plan_by_portion_keeps_the_rest(
    services: Services,
) -> None:
    services.absence.book(MONDAY, AbsenceType.ANNUAL, Portion.AM)
    services.absence.book(MONDAY, AbsenceType.SICK, Portion.PM)

    plan = services.absence.removal_plan(MONDAY, MONDAY, portion=Portion.PM)
    result = services.absence.remove_plan(plan)

    assert result.success
    assert plan.portion is Portion.PM
    assert plan.summary == "  1 afternoon of sickness"
    assert [
        (row.absence_type, row.portion)
        for row in services.absence.in_range(MONDAY, MONDAY)
    ] == [(AbsenceType.ANNUAL, Portion.AM)]


def test_empty_removal_plan_is_empty(services: Services) -> None:
    plan = services.absence.removal_plan(MONDAY, FRIDAY)
    assert plan.is_empty
    assert plan.count == 0
    assert plan.summary == ""


def test_one_of_a_kind_reads_in_the_singular(services: Services) -> None:
    services.absence.book_range(MONDAY, MONDAY, AbsenceType.SICK)
    services.absence.book_range(FRIDAY, FRIDAY, AbsenceType.FLEXI, Portion.PM)

    plan = services.absence.removal_plan(MONDAY, FRIDAY)

    assert plan.summary == ("  1 day of sickness\n  1 afternoon of TOIL")


def test_booking_added_after_confirmation_is_kept(
    services: Services,
) -> None:
    """The confirmed snapshot is all-or-nothing, not a licence to clear a span."""
    services.absence.book(MONDAY, AbsenceType.ANNUAL, Portion.AM)
    confirmed = services.absence.removal_plan(MONDAY, FRIDAY)
    services.absence.book(MONDAY, AbsenceType.SICK, Portion.PM)

    result = services.absence.remove_plan(confirmed)

    assert result.skipped == ((MONDAY, PLAN_CHANGED),)
    assert [
        (row.absence_type, row.portion)
        for row in services.absence.in_range(MONDAY, FRIDAY)
    ] == [
        (AbsenceType.ANNUAL, Portion.AM),
        (AbsenceType.SICK, Portion.PM),
    ]
