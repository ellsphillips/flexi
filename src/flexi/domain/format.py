"""Durations and dates as the strings a reader compares down a column."""

from __future__ import annotations

from datetime import date, datetime, timedelta

MINUS = "−"
"""U+2212, not a hyphen: drawn at digit width, so a column of deltas aligns."""

ZERO = "0:00"


def hm(value: timedelta) -> str:
    """A duration as ``h:mm``, unsigned, rounding toward zero.

    Examples:
        >>> hm(timedelta(hours=7, minutes=24))
        '7:24'
        >>> hm(timedelta(minutes=8))
        '0:08'
    """
    total = int(abs(value).total_seconds())
    return f"{total // 3600}:{total % 3600 // 60:02d}"


def hms(value: timedelta) -> str:
    """A duration as ``h:mm:ss``, for the live readout.

    Examples:
        >>> hms(timedelta(hours=2, minutes=14, seconds=3))
        '2:14:03'
    """
    total = int(abs(value).total_seconds())
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def delta(value: timedelta) -> str:
    """A signed duration. Zero carries no sign, because it is not a small surplus.

    Examples:
        >>> delta(timedelta(minutes=48))
        '+0:48'
        >>> delta(timedelta(hours=-4, minutes=-14))
        '−4:14'
        >>> delta(timedelta())
        '0:00'
    """
    total = int(value.total_seconds())
    if total == 0:
        return ZERO
    return f"{'+' if total > 0 else MINUS}{hm(value)}"


def digits(value: timedelta) -> str:
    """A signed duration for Textual's ``Digits``, whose glyph set has no U+2212.

    Examples:
        >>> digits(timedelta(hours=-10, minutes=-50))
        '-10:50'
        >>> digits(timedelta(hours=12, minutes=40))
        '+12:40'
    """
    total = int(value.total_seconds())
    if total == 0:
        return ZERO
    return f"{'+' if total > 0 else '-'}{hm(value)}"


def stamp(when: date, pattern: str) -> str:
    """``strftime`` with an unpadded day, on every platform.

    ``%-d`` is a glibc and BSD extension; on Windows it raises rather than
    dropping the zero, so the day is substituted before ``strftime`` sees it.

    Examples:
        >>> stamp(date(2026, 6, 5), "%-d %b")
        '5 Jun'
    """
    return when.strftime(pattern.replace("%-d", str(when.day)))


def long_date(when: date) -> str:
    """A date with its weekday and year.

    Examples:
        >>> long_date(date(2026, 6, 11))
        'Thu 11 Jun 2026'
    """
    return stamp(when, "%a %-d %b %Y")


def short_date(when: date) -> str:
    """A date with its weekday.

    Examples:
        >>> short_date(date(2026, 6, 11))
        'Thu 11 Jun'
    """
    return stamp(when, "%a %-d %b")


def day_month(when: date) -> str:
    """A date at its shortest.

    Examples:
        >>> day_month(date(2026, 6, 11))
        '11 Jun'
    """
    return stamp(when, "%-d %b")


def clock(moment: datetime) -> str:
    """A wall-clock time.

    Examples:
        >>> clock(datetime(2026, 6, 11, 9, 12))
        '09:12'
    """
    return moment.strftime("%H:%M")


def days(value: float) -> str:
    """A count of days, with a half only when there is one.

    Examples:
        >>> days(18.5)
        '18.5'
        >>> days(19.0)
        '19'
    """
    return f"{value:g}"


def signed_days(value: float) -> str:
    """A signed count of days.

    Examples:
        >>> signed_days(-1.5)
        '−1.5'
        >>> signed_days(2.0)
        '+2'
        >>> signed_days(0.0)
        '0'
    """
    if value == 0:
        return "0"
    return f"{'+' if value > 0 else MINUS}{days(abs(value))}"
