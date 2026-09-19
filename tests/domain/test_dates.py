"""Reading a date typed at a command line, and shifting one.

`add_months` clamps into a shorter month and `days_between` is inclusive at
both ends, so both are pinned below along with the words, spans and offsets
the parser accepts.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from flexi.domain import leaveyear
from flexi.domain.dates import (
    DATE_RANGE_ERROR,
    MONTH_NAMES,
    SUPPORTED_FIRST,
    SUPPORTED_LAST,
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
def test_relative_words(typed: str, expected: date) -> None:
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
def test_bare_weekday_is_the_next_one_or_today(typed: str, expected: date) -> None:
    assert parse_date(typed, reference=MONDAY) == expected


def test_next_weekday_never_means_today() -> None:
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
def test_written_forms(typed: str, expected: date) -> None:
    assert parse_date(typed, reference=MONDAY) == expected


def test_month_names_ignore_the_process_locale() -> None:
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
def test_offsets(typed: str, expected: date) -> None:
    assert parse_date(typed, reference=MONDAY) == expected


def test_month_offset_clamps_to_a_shorter_month() -> None:
    assert parse_date("+1m", reference=date(2026, 1, 31)) == date(2026, 2, 28)


# ---------- the two preferences ----------


def test_dialog_reads_a_bare_day_as_this_month() -> None:
    """Looking at August and typing 5 means the 5th of August, passed or not."""
    assert parse_date("5", reference=MONDAY) == date(2026, 8, 5)


def test_booking_reads_a_bare_day_as_the_next_one() -> None:
    """Leave is booked forwards. The 5th has gone, so it means September."""
    assert parse_date("5", reference=MONDAY, prefer=Preference.FORWARD) == date(
        2026, 9, 5
    )


def test_booking_skips_months_without_that_day() -> None:
    """The next date carrying that day number, which is not always next month."""
    assert parse_date(
        "30", reference=date(2026, 2, 10), prefer=Preference.FORWARD
    ) == date(2026, 3, 30)


def test_booking_forward_does_not_clamp_into_february() -> None:
    """The 30th, asked for on the 31st of January, is the 30th of March."""
    assert parse_date(
        "30", reference=date(2026, 1, 31), prefer=Preference.FORWARD
    ) == date(2026, 3, 30)


def test_day_32_is_refused_when_booking_forwards() -> None:
    with pytest.raises(ValueError, match="has no day 32"):
        parse_date("32", reference=MONDAY, prefer=Preference.FORWARD)


def test_month_that_has_passed_books_next_year() -> None:
    assert parse_date("12 jun", reference=MONDAY, prefer=Preference.FORWARD) == date(
        2027, 6, 12
    )


def test_month_still_to_come_stays_this_year() -> None:
    assert parse_date("12 dec", reference=MONDAY, prefer=Preference.FORWARD) == date(
        2026, 12, 12
    )


@pytest.mark.parametrize("typed", ["29 feb", "feb 29", "29/02"])
def test_yearless_leap_day_finds_the_next_real_one(typed: str) -> None:
    assert parse_date(
        typed, reference=date(2026, 8, 10), prefer=Preference.FORWARD
    ) == date(2028, 2, 29)
    assert parse_date(
        typed, reference=date(2028, 2, 29), prefer=Preference.FORWARD
    ) == date(2028, 2, 29)
    assert parse_date(
        typed, reference=date(2028, 3, 1), prefer=Preference.FORWARD
    ) == date(2032, 2, 29)


def test_feb_29_is_refused_in_a_common_year() -> None:
    with pytest.raises(ValueError, match="February 2026 has no day 29"):
        parse_date("29 feb", reference=date(2026, 8, 10))


def test_explicit_leap_year_ignores_the_preference() -> None:
    assert parse_date(
        "29 feb 2028", reference=date(2030, 1, 1), prefer=Preference.FORWARD
    ) == date(2028, 2, 29)


# ---------- spans ----------


@pytest.mark.parametrize("separator", ["to", "until", "through"])
def test_span_can_be_written_several_ways(separator: str) -> None:
    assert parse_span(f"monday {separator} friday", reference=MONDAY) == (
        MONDAY,
        FRIDAY,
    )


def test_span_can_use_dots() -> None:
    assert parse_span("monday..friday", reference=MONDAY) == (MONDAY, FRIDAY)


def test_span_end_is_read_from_its_start() -> None:
    """Otherwise 28 Dec to 4 Jan books eleven months backwards."""
    assert parse_span("28 dec to 4 jan", reference=MONDAY) == (
        date(2026, 12, 28),
        date(2027, 1, 4),
    )


def test_weekday_span_stays_inside_one_week() -> None:
    assert parse_span("friday to monday", reference=MONDAY) == (
        FRIDAY,
        date(2026, 8, 17),
    )


def test_one_date_is_a_span_of_one_day() -> None:
    assert parse_span("tomorrow", reference=MONDAY) == (
        date(2026, 8, 11),
        date(2026, 8, 11),
    )


def test_backwards_range_is_refused() -> None:
    with pytest.raises(ValueError, match="runs backwards"):
        parse_span("2026-09-10 to 2026-08-10", reference=MONDAY)


# ---------- refusals ----------


@pytest.mark.parametrize("typed", ["", "   ", "someday", "next someday", "12 smarch"])
def test_it_refuses_what_it_cannot_read(typed: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        parse_date(typed, reference=MONDAY)


def test_refusal_names_the_forms_it_understands() -> None:
    with pytest.raises(ValueError, match="friday") as raised:
        parse_date("whenever", reference=MONDAY)
    assert "+3d" in str(raised.value)


def test_day_the_month_lacks_is_refused() -> None:
    """Reading a date, not booking one, so no later month is tried."""
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


def test_offset_past_the_calendar_is_a_value_error() -> None:
    """`date` answers an offset this large with `OverflowError`.

    Every caller catches the `ValueError` that `parse_date` documents, so the
    overflow is converted before it leaves the parser.
    """
    with pytest.raises(ValueError, match="outside") as raised:
        parse_date("+999999999999d", reference=date(2026, 6, 11))

    assert str(raised.value) == DATE_RANGE_ERROR


@pytest.mark.parametrize("typed", ["31/02/2026", "29/02/2026", "31/04/2026"])
def test_written_date_must_name_a_real_day(typed: str) -> None:
    """With a year given there is nothing to resolve, so the reader declines.

    It answers `None` and the next reader tries, which ends in the help string
    instead of a `ValueError` raised inside `date`.
    """
    with pytest.raises(ValueError, match="Try"):
        parse_date(typed, reference=date(2026, 6, 11))


def test_an_impossible_month_day_pair_is_refused() -> None:
    """The 30th of February cannot be a day in any year, so no year is chosen."""
    with pytest.raises(ValueError, match="not a valid calendar day"):
        resolve_month_day(2, 30, date(2026, 6, 11), Preference.CURRENT)


# ---------- arithmetic ----------


def test_span_of_days_includes_both_of_its_ends() -> None:
    """A fortnight booked Monday to the Friday after is ten working days."""
    june = date(2026, 6, 1)
    assert days_between(june, june) == [june]
    assert days_between(june, date(2026, 6, 5)) == [
        date(2026, 6, day) for day in range(1, 6)
    ]


def test_backwards_span_holds_no_days() -> None:
    """Every caller refuses a backwards span first, so the answer is no days."""
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


# ---------- a span's end ----------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("yesterday to tomorrow", (date(2026, 8, 9), date(2026, 8, 11))),
        ("last week to today", (date(2026, 8, 3), MONDAY)),
        ("last week to yesterday", (date(2026, 8, 3), date(2026, 8, 9))),
        ("2026-08-03 to today", (date(2026, 8, 3), MONDAY)),
        ("+1d to +3d", (date(2026, 8, 11), date(2026, 8, 13))),
    ],
)
def test_relative_span_end_counts_from_today(
    typed: str, expected: tuple[date, date]
) -> None:
    """Read from the start instead, `last week to today` is a single day."""
    assert parse_span(typed, reference=MONDAY) == expected


def test_relative_end_before_its_start_is_refused() -> None:
    """Friday comes after today, so `friday to today` runs backwards."""
    with pytest.raises(ValueError, match="runs backwards"):
        parse_span("friday to today", reference=MONDAY)


# ---------- numbers no calendar has ----------


@pytest.mark.parametrize(
    ("read", "message"),
    [
        pytest.param(
            lambda: parse_date("2147483648", reference=MONDAY),
            "August has no day 2147483648",
            id="bare-day",
        ),
        pytest.param(
            lambda: parse_date(
                "2147483648", reference=MONDAY, prefer=Preference.FORWARD
            ),
            "August has no day 2147483648",
            id="bare-day-forward",
        ),
        pytest.param(
            lambda: parse_date("2147483648 jun", reference=MONDAY),
            "2147483648/6 is not a valid calendar day",
            id="written",
        ),
        pytest.param(
            lambda: parse_date("jun 99999999999999999999 2026", reference=MONDAY),
            "Try 2026",
            id="written-with-a-year",
        ),
        pytest.param(
            lambda: parse_span("2026-08-10 to 99999999999999999999", reference=MONDAY),
            "August has no day 99999999999999999999",
            id="span",
        ),
        pytest.param(
            lambda: parse_day_of_month("2147483648", MONDAY),
            "August has no day 2147483648",
            id="day-of-month",
        ),
        pytest.param(
            lambda: resolve_month_day(6, 2147483648, MONDAY),
            "2147483648/6 is not a valid calendar day",
            id="month-day",
        ),
    ],
)
def test_day_number_too_large_is_refused(
    read: Callable[[], object], message: str
) -> None:
    """`date` answers a day of 2147483648 with `OverflowError`, not `ValueError`.

    Every caller of the parser catches the documented failure only.
    """
    with pytest.raises(ValueError, match=message):
        read()


def test_only_decimal_digits_are_day_numbers() -> None:
    """`'²'.isdigit()` is true and `int('²')` raises, so the test is decimal.

    Arabic-Indic digits are decimal and stay a day of the month.
    """
    with pytest.raises(ValueError, match="Try 2026"):
        parse_date("²", reference=MONDAY)
    assert parse_date("٣", reference=MONDAY) == date(2026, 8, 3)


# ---------- the window Flexi works in ----------


@pytest.mark.parametrize(
    "read",
    [
        pytest.param(lambda: parse_date("9999-12-31", reference=MONDAY), id="last"),
        pytest.param(lambda: parse_date("0001-01-01", reference=MONDAY), id="first"),
        pytest.param(
            lambda: parse_span("2026-08-10 to 9999-12-31", reference=MONDAY),
            id="span-end",
        ),
        pytest.param(
            lambda: parse_span("today to next week", reference=date(9998, 12, 31)),
            id="span-word-end",
        ),
    ],
)
def test_date_outside_the_supported_window_is_refused(
    read: Callable[[], object],
) -> None:
    """`--as-of 9999-12-31` reaches `leaveyear.bounds`, which asks for year 10000.

    The refusal comes from the parser, where every caller already expects one.
    """
    with pytest.raises(ValueError, match="outside") as raised:
        read()

    assert str(raised.value) == DATE_RANGE_ERROR


@pytest.mark.parametrize("edge", [SUPPORTED_FIRST, SUPPORTED_LAST])
def test_both_edges_of_the_window_have_a_leave_year(edge: date) -> None:
    """A date the window accepts can be worked with."""
    start, end = leaveyear.bounds(edge, 4, 6)

    assert start <= edge <= end
