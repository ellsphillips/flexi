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
    """Rounds toward zero, so a target is not met a second early."""
    assert hm(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(18.5, "18.5"), (19.0, "19"), (0.0, "0")]
)
def test_days(value: float, expected: str) -> None:
    """Writes a half only when there is one."""
    assert days(value) == expected


# Properties: the shape never varies. The same width, the same sign
# convention, and a value that reads back off the screen.

HM = re.compile(r"^\d+:[0-5]\d$")
SIGNED = re.compile(r"^(\+|\u2212)?\d+:[0-5]\d$")

half_days = st.integers(min_value=-2000, max_value=2000).map(lambda n: n / 2)
"""Day counts as Flexi holds them: whole days and halves."""


@given(value=strategies.signed_durations)
def test_duration_always_reads_as_h_mm(value: timedelta) -> None:
    assert HM.match(hm(value)), hm(value)


@given(value=strategies.signed_durations)
def test_delta_can_be_read_back(value: timedelta) -> None:
    printed = delta(value)
    assert SIGNED.match(printed), printed

    sign = -1 if printed.startswith(MINUS) else 1
    hours, minutes = printed.lstrip("+" + MINUS).split(":")
    recovered = sign * timedelta(hours=int(hours), minutes=int(minutes))
    assert recovered == value, printed


@given(value=strategies.signed_durations)
def test_only_zero_goes_unsigned(value: timedelta) -> None:
    printed = delta(value)
    assert printed.startswith(("+", MINUS)) is bool(value)


@given(value=strategies.signed_durations)
def test_digits_differ_only_by_the_minus_glyph(value: timedelta) -> None:
    """Textual's `Digits` has no U+2212."""
    assert digits(value) == delta(value).replace(MINUS, "-")


@given(value=half_days)
def test_day_count_has_no_trailing_zero(value: float) -> None:
    printed = days(abs(value))
    assert not printed.endswith(".0")
    assert float(printed) == abs(value)


@given(value=half_days)
def test_signed_day_count_matches_the_unsigned_one(value: float) -> None:
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
    assert plural(count, noun) == (noun if count == 1 else noun + "s")


@given(when=strategies.dates)
def test_date_never_carries_a_padded_day(when: date) -> None:
    """`%-d` is a glibc extension; on Windows it raises."""
    assert long_date(when).split() == [
        when.strftime("%a"),
        str(when.day),
        when.strftime("%b"),
        str(when.year),
    ]
    assert short_date(when) == f"{when:%a} {when.day} {when:%b}"
    assert day_month(when) == f"{when.day} {when:%b}"
