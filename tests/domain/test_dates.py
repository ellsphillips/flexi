"""Every way somebody might type a date at a command line.

The parser used to live beside the "go to date" modal, accepted none of the
words a CLI user reaches for, and had no tests at all.
"""

from __future__ import annotations

from datetime import date

import pytest

from flexi.domain.dates import Preference, parse_date, parse_span

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
    assert parse_date(typed, today=MONDAY) == expected


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
    assert parse_date(typed, today=MONDAY) == expected


def test_next_weekday_never_means_today() -> None:
    """Said on a Monday, "next monday" is a week away, not now."""
    assert parse_date("next monday", today=MONDAY) == date(2026, 8, 17)


def test_last_weekday_means_the_one_just_gone() -> None:
    """On a Friday, "last friday" is seven days back, not today."""
    assert parse_date("last friday", today=FRIDAY) == date(2026, 8, 7)
    assert parse_date("last friday", today=MONDAY) == date(2026, 8, 7)


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
    assert parse_date(typed, today=MONDAY) == expected


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
    assert parse_date(typed, today=MONDAY) == expected


def test_a_month_offset_clamps_to_a_shorter_month() -> None:
    assert parse_date("+1m", today=date(2026, 1, 31)) == date(2026, 2, 28)


# -- the two preferences ----------------------------------------------------


def test_a_dialog_reads_a_bare_day_as_this_month() -> None:
    """Looking at August and typing 5 means the 5th of August, passed or not."""
    assert parse_date("5", today=MONDAY) == date(2026, 8, 5)


def test_booking_reads_a_bare_day_as_the_next_one() -> None:
    """Leave is booked forwards. The 5th has gone, so it means September."""
    assert parse_date("5", today=MONDAY, prefer=Preference.FORWARD) == date(2026, 9, 5)


def test_a_month_that_has_passed_books_next_year() -> None:
    assert parse_date("12 jun", today=MONDAY, prefer=Preference.FORWARD) == date(
        2027, 6, 12
    )


def test_a_month_still_to_come_stays_this_year() -> None:
    assert parse_date("12 dec", today=MONDAY, prefer=Preference.FORWARD) == date(
        2026, 12, 12
    )


# -- spans -------------------------------------------------------------------


@pytest.mark.parametrize("separator", ["to", "until", "through"])
def test_a_span_can_be_written_several_ways(separator: str) -> None:
    assert parse_span(f"monday {separator} friday", today=MONDAY) == (MONDAY, FRIDAY)


def test_a_span_can_use_dots() -> None:
    assert parse_span("monday..friday", today=MONDAY) == (MONDAY, FRIDAY)


def test_the_end_is_read_from_the_start_not_from_today() -> None:
    """Otherwise 28 Dec to 4 Jan books eleven months backwards."""
    assert parse_span("28 dec to 4 jan", today=MONDAY) == (
        date(2026, 12, 28),
        date(2027, 1, 4),
    )


def test_a_weekday_span_stays_inside_one_week() -> None:
    assert parse_span("friday to monday", today=MONDAY) == (FRIDAY, date(2026, 8, 17))


def test_one_date_is_a_span_of_one_day() -> None:
    assert parse_span("tomorrow", today=MONDAY) == (
        date(2026, 8, 11),
        date(2026, 8, 11),
    )


def test_a_backwards_range_is_refused() -> None:
    """A transposed range used to plan most of a year of annual leave."""
    with pytest.raises(ValueError, match="runs backwards"):
        parse_span("2026-09-10 to 2026-08-10", today=MONDAY)


# -- refusals ----------------------------------------------------------------


@pytest.mark.parametrize("typed", ["", "   ", "someday", "next someday", "12 smarch"])
def test_it_refuses_what_it_cannot_read(typed: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        parse_date(typed, today=MONDAY)


def test_the_refusal_names_the_forms_it_understands() -> None:
    with pytest.raises(ValueError, match="friday") as raised:
        parse_date("whenever", today=MONDAY)
    assert "+3d" in str(raised.value)


def test_a_day_the_month_does_not_have() -> None:
    with pytest.raises(ValueError, match="February has no day 30"):
        parse_date("30", today=date(2026, 2, 10))
