import re
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from flexi.domain.format import (
    MINUS,
    day_month,
    days,
    delta,
    digits,
    hm,
    long_date,
    plural,
    short_date,
    signed_days,
)
from tests import strategies


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (timedelta(hours=7, minutes=24), "7:24"),
        (timedelta(minutes=8), "0:08"),
        (timedelta(), "0:00"),
        (timedelta(hours=-7, minutes=-24), "7:24"),
        (timedelta(hours=1, minutes=59, seconds=59), "1:59"),
    ],
)
def test_hm(value: timedelta, expected: str) -> None:
    """It rounds toward zero, so a target is not met a second early."""
    assert hm(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(18.5, "18.5"), (19.0, "19"), (0.0, "0")]
)
def test_days(value: float, expected: str) -> None:
    """It writes a half only when there is one."""
    assert days(value) == expected


# -- properties ------------------------------------------------------------
#
# Every one of these is read by somebody scanning a column, so what matters is
# not one example but that the shape never varies: the same width, the same
# sign convention, and a value that can be read back off the screen.

HM = re.compile(r"^\d+:[0-5]\d$")
SIGNED = re.compile(r"^(\+|\u2212)?\d+:[0-5]\d$")

half_days = st.integers(min_value=-2000, max_value=2000).map(lambda n: n / 2)
"""Day counts as Flexi actually holds them: whole days and halves."""


@given(value=strategies.signed_durations)
def test_a_duration_always_reads_as_hours_and_two_digits_of_minutes(
    value: timedelta,
) -> None:
    """A column of durations only lines up if every one is the same shape."""
    assert HM.match(hm(value)), hm(value)


@given(value=strategies.signed_durations)
def test_a_delta_can_be_read_back_off_the_screen(value: timedelta) -> None:
    """What is printed is the value, to the minute it was truncated to."""
    printed = delta(value)
    assert SIGNED.match(printed), printed

    sign = -1 if printed.startswith(MINUS) else 1
    hours, minutes = printed.lstrip("+" + MINUS).split(":")
    recovered = sign * timedelta(hours=int(hours), minutes=int(minutes))
    assert recovered == value, printed


@given(value=strategies.signed_durations)
def test_only_zero_goes_unsigned(value: timedelta) -> None:
    """Zero is not a small surplus, and must not be dressed as one."""
    printed = delta(value)
    assert printed.startswith(("+", MINUS)) is bool(value)


@given(value=strategies.signed_durations)
def test_digits_says_the_same_thing_without_the_glyph(value: timedelta) -> None:
    """Textual's `Digits` has no U+2212, so the two differ in that character alone."""
    assert digits(value) == delta(value).replace(MINUS, "-")


@given(value=half_days)
def test_a_day_count_never_shows_a_trailing_zero(value: float) -> None:
    """A reader parses `19.0 days left`; they read `19 days left`."""
    printed = days(abs(value))
    assert not printed.endswith(".0")
    assert float(printed) == abs(value)


@given(value=half_days)
def test_a_signed_day_count_agrees_with_the_unsigned_one(value: float) -> None:
    printed = signed_days(value)
    if value == 0:
        assert printed == "0"
        return
    assert printed[0] == ("+" if value > 0 else MINUS)
    assert printed[1:] == days(abs(value))


@given(
    count=half_days,
    noun=st.sampled_from(["day", "bank holiday", "occasion"]),
)
def test_only_exactly_one_is_singular(count: float, noun: str) -> None:
    """Half a day is not one of anything, and neither is none of them."""
    assert plural(count, noun) == (noun if count == 1 else noun + "s")


@given(when=strategies.dates)
def test_a_date_never_carries_a_padded_day(when: date) -> None:
    """`%-d` is a glibc extension: on Windows it raises rather than dropping it."""
    assert long_date(when).split() == [
        when.strftime("%a"),
        str(when.day),
        when.strftime("%b"),
        str(when.year),
    ]
    assert short_date(when) == f"{when:%a} {when.day} {when:%b}"
    assert day_month(when) == f"{when.day} {when:%b}"
