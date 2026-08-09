"""Turning durations into the strings a reader compares.

One rule holds this together: **a signed figure uses U+2212 MINUS SIGN, not a
hyphen.** In a monospace terminal a hyphen is drawn a third of the width of a
digit and sits on the baseline, so a column of deltas with hyphens is visibly
ragged and reads as a list of ranges. The minus sign is drawn at digit width at
the same height as the plus, which is what makes ``+0:48`` and ``−4:14`` line up.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

MINUS = "−"
"""U+2212. Not a hyphen — see the module docstring."""

ZERO = "0:00"


def hm(value: timedelta) -> str:
    """A duration as ``h:mm``, always positive.

    Rounds toward zero on seconds, because a clock reading ``7:24`` that is
    really 7:24:59 is closer to the truth than one that rounds up past a target
    the wearer has not actually met.

    Examples:
        >>> hm(timedelta(hours=7, minutes=24))
        '7:24'
        >>> hm(timedelta(minutes=8))
        '0:08'
    """
    total = int(abs(value).total_seconds())
    return f"{total // 3600}:{total % 3600 // 60:02d}"


def hms(value: timedelta) -> str:
    """A duration as ``h:mm:ss``, for the live elapsed readout.

    Examples:
        >>> hms(timedelta(hours=2, minutes=14, seconds=3))
        '2:14:03'
    """
    total = int(abs(value).total_seconds())
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def delta(value: timedelta) -> str:
    """A signed duration: ``+0:48``, ``−4:14``, or ``0:00`` unsigned.

    Zero carries no sign, because ``+0:00`` reads as a small surplus and the
    whole point of the figure is that there is not one.

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
    """A signed duration for Textual's ``Digits`` widget.

    ``Digits`` renders a fixed 3×3 glyph set — ``" 0123456789+-^x:ABCDEF$£€()"``
    — which has no U+2212, so the one place in Flexi that draws big numbers uses
    an ASCII hyphen. It costs nothing: the glyph is full width, so the column
    still aligns, and the sign is drawn in the deficit colour besides.

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

    ``%-d`` is a glibc and BSD extension. On Windows it does not mean "the day
    without a leading zero", it raises — so the day is substituted before the
    pattern reaches ``strftime`` and the result is identical everywhere.

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
    """A date with its weekday, for a header that already implies the year.

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
    """A wall-clock time as ``09:12``.

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
    """A signed count of days, using the same minus sign as :func:`delta`.

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
