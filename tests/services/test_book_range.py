"""Booking a span: partial by design, and honest about what it skipped."""

from __future__ import annotations

from datetime import date, timedelta

from flexi.constants import AbsenceType, Portion
from flexi.services.absence import PLAN_CHANGED, RemovalBooking
from flexi.services.registry import Services
from tests.services.conftest import DEFAULT_HOLIDAY

MONDAY = date(2026, 8, 10)
FRIDAY = date(2026, 8, 14)
NEXT_FRIDAY = date(2026, 8, 21)
BANK_HOLIDAY = DEFAULT_HOLIDAY


def test_a_working_week_books_five_days(services: Services) -> None:
    """Monday to Friday is five, not seven."""
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 5
    assert not result.skipped


def test_weekends_are_skipped_quietly(services: Services) -> None:
    """Nobody booking a fortnight means to book the Saturdays.

    Reporting them as refusals would bury the one refusal that matters.
    """
    result = services.absence.book_range(MONDAY, NEXT_FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 10
    assert not result.skipped
    assert "10 days" in result.message("booked")


def test_a_bank_holiday_is_skipped_quietly_too(services: Services) -> None:
    """It is not yours to book, and it is not a mistake that you tried."""
    result = services.absence.book_range(
        BANK_HOLIDAY, BANK_HOLIDAY + timedelta(days=4), AbsenceType.ANNUAL
    )
    assert BANK_HOLIDAY not in result.booked
    assert len(result.booked) == 4
    assert not result.skipped


def test_a_day_already_booked_is_reported(services: Services) -> None:
    """This one is worth saying: something is there that you did not expect."""
    services.absence.book(date(2026, 8, 12), AbsenceType.SICK)
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 4
    assert [when for when, _ in result.skipped] == [date(2026, 8, 12)]
    assert "already booked" in result.message("booked")


def test_running_out_of_leave_mid_range_books_what_it_can(services: Services) -> None:
    """Partial, and it says how far it got."""
    services.settings.save_entitlement(2025, 2.0)
    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)
    assert len(result.booked) == 2
    assert len(result.skipped) == 3
    assert "Not enough annual leave" in result.message("booked")


def test_toil_across_a_range_warns_once(services: Services) -> None:
    """Not five times for five days."""
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
    """And says what it removed, not what was already free."""
    services.absence.book_range(MONDAY, date(2026, 8, 12), AbsenceType.ANNUAL)
    result = services.absence.clear_range(MONDAY, FRIDAY)
    assert len(result.booked) == 3
    assert not result.skipped
    assert services.absence.in_range(MONDAY, FRIDAY) == []


def test_clearing_an_empty_range_says_so(services: Services) -> None:
    result = services.absence.clear_range(MONDAY, FRIDAY)
    assert not result.success
    assert result.message("removed") == "Nothing to do"


def test_a_span_that_books_nothing_gives_the_reason_unedited(
    services: Services,
) -> None:
    """One refusal is a sentence, not a report on a span.

    "Nothing booked: That day is already booked in full" is the status bar
    apologising for itself. When every day was turned down for the same reason,
    the reason *is* the answer.
    """
    services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    again = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    assert not again.success
    assert len(again.skipped) == 5
    assert again.message("booked") == "That day is already booked in full"


def test_a_span_refused_for_two_different_reasons_names_both(
    services: Services,
) -> None:
    """Each reason once, however many days it accounts for.

    Five days short of leave and one already booked is two things to fix, and
    listing the same sentence four times over would hide the second.
    """
    services.absence.book(MONDAY, AbsenceType.SICK)
    services.settings.save_entitlement(2025, 0.0)

    result = services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    assert not result.success
    assert len(result.skipped) == 5
    assert result.message("booked") == (
        "Nothing booked: That day is already booked in full; "
        "Not enough annual leave — 1 day short of the request"
    )


def test_a_single_day_reads_as_a_day(services: Services) -> None:
    """'1 day booked', not '1 days booked'."""
    result = services.absence.book_range(MONDAY, MONDAY, AbsenceType.SICK)
    assert result.message("booked") == "1 day booked"


def test_a_removal_plan_names_what_would_go(services: Services) -> None:
    """A bare count is something to take on trust; a list is something to read.

    Nine days of annual leave and nine sick mornings are not the same thing to
    agree to, and the old question could not tell them apart.
    """
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


def test_a_removal_plan_removes_nothing(services: Services) -> None:
    """Planning is the half that does not write, on this side too."""
    services.absence.book_range(MONDAY, FRIDAY, AbsenceType.ANNUAL)

    services.absence.removal_plan(MONDAY, FRIDAY)

    assert len(services.absence.in_range(MONDAY, FRIDAY)) == 5


def test_a_portion_specific_removal_plan_preserves_the_other_half(
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


def test_an_empty_removal_plan_is_empty(services: Services) -> None:
    plan = services.absence.removal_plan(MONDAY, FRIDAY)
    assert plan.is_empty
    assert plan.count == 0
    assert plan.summary == ""


def test_one_of_a_kind_reads_in_the_singular(services: Services) -> None:
    services.absence.book_range(MONDAY, MONDAY, AbsenceType.SICK)
    services.absence.book_range(FRIDAY, FRIDAY, AbsenceType.FLEXI, Portion.PM)

    plan = services.absence.removal_plan(MONDAY, FRIDAY)

    assert plan.summary == ("  1 day of sickness\n  1 afternoon of TOIL")


def test_a_booking_added_after_confirmation_is_not_removed(
    services: Services,
) -> None:
    """The confirmed snapshot is all-or-nothing, not permission to clear a span."""
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
