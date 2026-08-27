"""Every way somebody might type a date at a command line, and shifting one.

The parser used to live beside the "go to date" modal, accepted none of the
words a CLI user reaches for, and had no tests at all.

`add_months` and `days_between` were four implementations across the widgets and
the services before they were one here, so the cases that separated them --
clamping a short month, and a span that runs backwards -- are pinned below.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from flexi.domain.dates import (
    DATE_RANGE_ERROR,
    MONTH_NAMES,
    Preference,
    add_days,
    add_months,
    days_between,
    forward_if_passed,
    month_index,
    parse_date,
    parse_day_of_month,
    parse_offset,
    parse_span,
    parse_weekday,
    parse_written,
    relative_to,
    resolve_month_day,
)

MONDAY = date(2026, 8, 10)
FRIDAY = date(2026, 8, 14)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("today", MONDAY),
        ("tomorrow", date(2026, 8, 11)),
        ("yesterday", date(2026, 8, 9)),
        ("next week", date(2026, 8, 17)),
        ("last week", date(2026, 8, 3)),
    ],
)
def test_the_relative_words(typed: str, expected: date) -> None:
    assert parse_date(typed, reference=MONDAY) == expected


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("friday", FRIDAY),
        ("fri", FRIDAY),
        ("FRIDAY", FRIDAY),
        ("monday", MONDAY),
        ("sunday", date(2026, 8, 16)),
    ],
)
def test_a_bare_weekday_is_the_next_one_and_today_counts(
    typed: str, expected: date
) -> None:
    assert parse_date(typed, reference=MONDAY) == expected


def test_next_weekday_never_means_today() -> None:
    """Said on a Monday, "next monday" is a week away, not now."""
    assert parse_date("next monday", reference=MONDAY) == date(2026, 8, 17)


def test_last_weekday_means_the_one_just_gone() -> None:
    """On a Friday, "last friday" is seven days back, not today."""
    assert parse_date("last friday", reference=FRIDAY) == date(2026, 8, 7)
    assert parse_date("last friday", reference=MONDAY) == date(2026, 8, 7)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("2026-06-12", date(2026, 6, 12)),
        ("12 jun 2026", date(2026, 6, 12)),
        ("12 june 2026", date(2026, 6, 12)),
        ("jun 12", date(2026, 6, 12)),
        ("12/06", date(2026, 6, 12)),
        ("12", date(2026, 8, 12)),
    ],
)
def test_the_written_forms(typed: str, expected: date) -> None:
    assert parse_date(typed, reference=MONDAY) == expected


def test_english_month_names_do_not_come_from_the_process_locale() -> None:
    assert MONTH_NAMES[5] == "june"
    assert month_index("jun") == month_index("June") == 6
    assert month_index("juin") is None
    with pytest.raises(ValueError, match="Try 2026"):
        parse_date("12 juin", reference=MONDAY)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("+3d", date(2026, 8, 13)),
        ("-2w", date(2026, 7, 27)),
        ("+1m", date(2026, 9, 10)),
        ("+1y", date(2027, 8, 10)),
    ],
)
def test_the_offsets(typed: str, expected: date) -> None:
    assert parse_date(typed, reference=MONDAY) == expected


def test_a_month_offset_clamps_to_a_shorter_month() -> None:
    assert parse_date("+1m", reference=date(2026, 1, 31)) == date(2026, 2, 28)


# -- the two preferences ----------------------------------------------------


def test_a_dialog_reads_a_bare_day_as_this_month() -> None:
    """Looking at August and typing 5 means the 5th of August, passed or not."""
    assert parse_date("5", reference=MONDAY) == date(2026, 8, 5)


def test_booking_reads_a_bare_day_as_the_next_one() -> None:
    """Leave is booked forwards. The 5th has gone, so it means September."""
    assert parse_date("5", reference=MONDAY, prefer=Preference.FORWARD) == date(
        2026, 9, 5
    )


def test_booking_a_day_this_month_does_not_have_finds_one_that_does() -> None:
    """The next date with that day number on it, which is not always next month.

    `flexi leave annual 30` in February was refused outright -- "February has
    no day 30", for a day somebody was entitled to ask for -- because the day
    was landed in today's month before the preference was consulted.
    """
    assert parse_date(
        "30", reference=date(2026, 2, 10), prefer=Preference.FORWARD
    ) == date(2026, 3, 30)


def test_booking_forward_over_a_short_month_does_not_clamp_into_it() -> None:
    """The 30th, asked for on the 31st of January, is the 30th of March.

    It used to take the 30th of January one month forward and clamp, booking
    the 28th of February -- a different day, silently, for a request that could
    not have meant it.
    """
    assert parse_date(
        "30", reference=date(2026, 1, 31), prefer=Preference.FORWARD
    ) == date(2026, 3, 30)


def test_a_day_no_month_has_is_still_refused_when_booking_forwards() -> None:
    """32 is a typo whichever way the preference points."""
    with pytest.raises(ValueError, match="has no day 32"):
        parse_date("32", reference=MONDAY, prefer=Preference.FORWARD)


def test_a_month_that_has_passed_books_next_year() -> None:
    assert parse_date("12 jun", reference=MONDAY, prefer=Preference.FORWARD) == date(
        2027, 6, 12
    )


def test_a_month_still_to_come_stays_this_year() -> None:
    assert parse_date("12 dec", reference=MONDAY, prefer=Preference.FORWARD) == date(
        2026, 12, 12
    )


@pytest.mark.parametrize("typed", ["29 feb", "feb 29", "29/02"])
def test_a_yearless_leap_day_finds_the_next_real_occurrence(typed: str) -> None:
    assert parse_date(
        typed, reference=date(2026, 8, 10), prefer=Preference.FORWARD
    ) == date(2028, 2, 29)
    assert parse_date(
        typed, reference=date(2028, 2, 29), prefer=Preference.FORWARD
    ) == date(2028, 2, 29)
    assert parse_date(
        typed, reference=date(2028, 3, 1), prefer=Preference.FORWARD
    ) == date(2032, 2, 29)


def test_a_leap_day_in_the_current_common_year_is_not_clamped() -> None:
    with pytest.raises(ValueError, match="February 2026 has no day 29"):
        parse_date("29 feb", reference=date(2026, 8, 10))


def test_an_explicit_leap_year_is_not_moved_by_a_forward_preference() -> None:
    assert parse_date(
        "29 feb 2028", reference=date(2030, 1, 1), prefer=Preference.FORWARD
    ) == date(2028, 2, 29)


# -- spans -------------------------------------------------------------------


@pytest.mark.parametrize("separator", ["to", "until", "through"])
def test_a_span_can_be_written_several_ways(separator: str) -> None:
    assert parse_span(f"monday {separator} friday", reference=MONDAY) == (
        MONDAY,
        FRIDAY,
    )


def test_a_span_can_use_dots() -> None:
    assert parse_span("monday..friday", reference=MONDAY) == (MONDAY, FRIDAY)


def test_the_end_is_read_from_the_start_not_from_today() -> None:
    """Otherwise 28 Dec to 4 Jan books eleven months backwards."""
    assert parse_span("28 dec to 4 jan", reference=MONDAY) == (
        date(2026, 12, 28),
        date(2027, 1, 4),
    )


def test_a_weekday_span_stays_inside_one_week() -> None:
    assert parse_span("friday to monday", reference=MONDAY) == (
        FRIDAY,
        date(2026, 8, 17),
    )


def test_one_date_is_a_span_of_one_day() -> None:
    assert parse_span("tomorrow", reference=MONDAY) == (
        date(2026, 8, 11),
        date(2026, 8, 11),
    )


def test_a_backwards_range_is_refused() -> None:
    """A transposed range used to plan most of a year of annual leave."""
    with pytest.raises(ValueError, match="runs backwards"):
        parse_span("2026-09-10 to 2026-08-10", reference=MONDAY)


# -- refusals ----------------------------------------------------------------


@pytest.mark.parametrize("typed", ["", "   ", "someday", "next someday", "12 smarch"])
def test_it_refuses_what_it_cannot_read(typed: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        parse_date(typed, reference=MONDAY)


def test_the_refusal_names_the_forms_it_understands() -> None:
    with pytest.raises(ValueError, match="friday") as raised:
        parse_date("whenever", reference=MONDAY)
    assert "+3d" in str(raised.value)


def test_a_day_the_month_does_not_have() -> None:
    """Reading a date, not booking one: February has no 30th and that is that."""
    with pytest.raises(ValueError, match="February has no day 30"):
        parse_date("30", reference=date(2026, 2, 10))


@pytest.mark.parametrize(
    "read",
    [
        pytest.param(lambda: add_days(date.max, 1), id="add-days"),
        pytest.param(lambda: add_months(date.max, 1), id="add-months"),
        pytest.param(lambda: relative_to("tomorrow", date.max), id="relative"),
        pytest.param(lambda: parse_weekday("next monday", date.max), id="weekday"),
        pytest.param(lambda: parse_offset("+999999999d", MONDAY), id="positive-offset"),
        pytest.param(lambda: parse_offset("-999999999w", MONDAY), id="negative-offset"),
        pytest.param(lambda: parse_date("+999999999d", reference=MONDAY), id="date"),
        pytest.param(
            lambda: parse_span("today to +999999999d", reference=MONDAY),
            id="span",
        ),
        pytest.param(
            lambda: parse_written("1 jan", date.max, Preference.FORWARD),
            id="written",
        ),
        pytest.param(
            lambda: parse_day_of_month("1", date.max, Preference.FORWARD),
            id="day-of-month",
        ),
        pytest.param(
            lambda: forward_if_passed(date(9999, 1, 1), date.max, Preference.FORWARD),
            id="forward-if-passed",
        ),
        pytest.param(
            lambda: resolve_month_day(1, 1, date.max, Preference.FORWARD),
            id="month-day",
        ),
    ],
)
def test_public_date_arithmetic_reports_range_errors_as_values(
    read: Callable[[], object],
) -> None:
    with pytest.raises(ValueError, match="outside") as raised:
        read()

    assert str(raised.value) == DATE_RANGE_ERROR


def test_an_offset_beyond_the_calendar_is_a_value_error_not_an_overflow() -> None:
    """`+999999999999d` is a typo, and `date` answers it with `OverflowError`.

    Every other unreadable date leaves `parse_date` as a `ValueError` naming the
    forms it understands. One arriving as an `OverflowError` would escape every
    caller that catches the documented one -- the CLI's `TypedDate` and the
    go-to-date modal both.
    """
    with pytest.raises(ValueError, match="outside") as raised:
        parse_date("+999999999999d", reference=date(2026, 6, 11))

    assert str(raised.value) == DATE_RANGE_ERROR


@pytest.mark.parametrize("typed", ["31/02/2026", "29/02/2026", "31/04/2026"])
def test_a_written_date_that_is_not_a_real_day_is_refused(typed: str) -> None:
    """A day the month does not have is not a date, however well spelled.

    With a year given there is nothing to resolve, so the reader has to decide:
    it answers `None` and lets the next reader try, which ends in the help
    string rather than a `ValueError` from inside `date`.
    """
    with pytest.raises(ValueError, match="Try"):
        parse_date(typed, reference=date(2026, 6, 11))


def test_resolving_a_month_and_day_that_no_year_has_says_so() -> None:
    """The 30th of February is not a date that a different year would fix.

    Distinct from the range error above: this is a pair that cannot be a day in
    any year, so it is refused before a year is chosen rather than after.
    """
    with pytest.raises(ValueError, match="not a valid calendar day"):
        resolve_month_day(2, 30, date(2026, 6, 11), Preference.CURRENT)


# -- arithmetic --------------------------------------------------------------


def test_a_span_of_days_includes_both_of_its_ends() -> None:
    """A fortnight booked Monday to the Friday after is ten working days.

    An exclusive end would quietly book nine of them.
    """
    june = date(2026, 6, 1)
    assert days_between(june, june) == [june]
    assert days_between(june, date(2026, 6, 5)) == [
        date(2026, 6, day) for day in range(1, 6)
    ]


def test_a_backwards_span_holds_no_days() -> None:
    """Not `[start]`.

    Every caller refuses a backwards span before it gets here, so the honest
    answer to "which dates lie between these two" is none of them. The three
    copies this replaced disagreed: two answered `[start]` and one answered
    nothing, and no caller could tell which it had.
    """
    assert days_between(date(2026, 6, 3), date(2026, 6, 1)) == []


@pytest.mark.parametrize(
    ("start", "count", "expected"),
    [
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
        (date(2026, 12, 15), 1, date(2027, 1, 15)),
        (date(2026, 1, 15), -1, date(2025, 12, 15)),
        (date(2026, 6, 30), 12, date(2027, 6, 30)),
        (date(2026, 6, 30), 0, date(2026, 6, 30)),
    ],
)
def test_moving_whole_months_clamps_to_a_shorter_one(
    start: date, count: int, expected: date
) -> None:
    """The 31st has no counterpart in February, and neither does 29 February."""
    assert add_months(start, count) == expected
