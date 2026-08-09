from datetime import datetime, timedelta

import pytest

from flexi.domain.format import MINUS, clock, days, delta, hm, hms, signed_days


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


def test_hms_carries_seconds_for_the_live_readout() -> None:
    """It shows seconds where a running clock is being watched."""
    assert hms(timedelta(hours=2, minutes=14, seconds=3)) == "2:14:03"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (timedelta(minutes=48), "+0:48"),
        (timedelta(hours=-4, minutes=-14), "−4:14"),
        (timedelta(), "0:00"),
    ],
)
def test_delta(value: timedelta, expected: str) -> None:
    """It signs a figure that is being compared to zero."""
    assert delta(value) == expected


def test_zero_carries_no_sign() -> None:
    """It does not write +0:00, which reads as a small surplus."""
    assert "+" not in delta(timedelta())
    assert MINUS not in delta(timedelta())


def test_a_negative_delta_uses_a_minus_sign_not_a_hyphen() -> None:
    """It aligns in a column, which a hyphen does not."""
    assert delta(timedelta(hours=-1)).startswith(MINUS)
    assert "-" not in delta(timedelta(hours=-1))


def test_signed_figures_are_the_same_width() -> None:
    """It draws + and − at the same width, so a column is not ragged."""
    assert len(delta(timedelta(minutes=48))) == len(delta(timedelta(minutes=-48)))


def test_clock() -> None:
    """It reads a wall-clock time."""
    assert clock(datetime(2026, 6, 11, 9, 12)) == "09:12"


@pytest.mark.parametrize(
    ("value", "expected"), [(18.5, "18.5"), (19.0, "19"), (0.0, "0")]
)
def test_days(value: float, expected: str) -> None:
    """It writes a half only when there is one."""
    assert days(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(-1.5, "−1.5"), (2.0, "+2"), (0.0, "0")]
)
def test_signed_days(value: float, expected: str) -> None:
    """It signs a day count the same way it signs a duration."""
    assert signed_days(value) == expected
