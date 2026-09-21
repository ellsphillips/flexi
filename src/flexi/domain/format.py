"""Durations and dates as the strings a reader compares down a column."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime, timedelta

__all__ = (
    "LEVEL",
    "MINUS",
    "MINUTES_PER_HOUR",
    "SECONDS_PER_MINUTE",
    "ZERO",
    "clock",
    "day_month",
    "days",
    "delta",
    "digits",
    "hm",
    "hm_hours",
    "hms",
    "is_level",
    "long_date",
    "month_title",
    "plural",
    "printable",
    "short_date",
    "signed_days",
    "spoken",
    "stamp",
    "whole_minutes",
)

MINUS = "−"
"""U+2212, not a hyphen: drawn at digit width, so a column of deltas aligns."""

ZERO = "0:00"

SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60

LEVEL = timedelta(minutes=1)
"""Below this, a balance is neither a surplus nor a deficit.

Every figure here is drawn in whole minutes, so anything smaller has no digit to
appear in, and a sign on a figure reading `0:00` claims a direction the number
does not show."""


def is_level(value: timedelta) -> bool:
    """True when a duration is too small for the minute these figures show.

    Examples:
        >>> is_level(timedelta(seconds=-30))
        True
        >>> is_level(timedelta())
        True
        >>> is_level(timedelta(minutes=-1))
        False
    """
    return abs(value) < LEVEL


def whole_minutes(value: timedelta) -> timedelta:
    """A duration floored to the minute every figure here is drawn in.

    Every reading is printed by :func:`hm`, which shows whole minutes, and a
    total is the sum of the readings. Flooring each term first is what makes the
    sum of what is shown equal the total that is shown.

    Examples:
        >>> whole_minutes(timedelta(minutes=2, seconds=9))
        datetime.timedelta(seconds=120)
        >>> whole_minutes(timedelta(minutes=-2, seconds=-9)) == timedelta(minutes=-3)
        True
    """
    return timedelta(minutes=value // timedelta(seconds=1) // SECONDS_PER_MINUTE)


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


def hm_hours(hours: float) -> str:
    """A signed count of hours as ``h:mm``, for an axis and not a reading.

    A plot works in floats, so converting back to a `timedelta` to reach `hm`
    would round twice, once into the duration and once out of it.

    Examples:
        >>> hm_hours(7.4)
        '7:24'
        >>> hm_hours(-1.5)
        '−1:30'
        >>> hm_hours(0.0)
        '0:00'
    """
    minutes = round(abs(hours) * MINUTES_PER_HOUR)
    sign = MINUS if hours < 0 and minutes else ""
    return f"{sign}{minutes // MINUTES_PER_HOUR}:{minutes % MINUTES_PER_HOUR:02d}"


def hms(value: timedelta) -> str:
    """A duration as ``h:mm:ss``, for the live readout.

    Examples:
        >>> hms(timedelta(hours=2, minutes=14, seconds=3))
        '2:14:03'
    """
    total = int(abs(value).total_seconds())
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def delta(value: timedelta) -> str:
    """A signed duration. Level carries no sign, because it is not a small surplus.

    Anything under a minute counts as level. `hm` truncates, so without that
    floor forty seconds of deficit would draw as `−0:00`: a sign, and the colour
    that goes with it, on a figure showing nothing.

    Examples:
        >>> delta(timedelta(minutes=48))
        '+0:48'
        >>> delta(timedelta(hours=-4, minutes=-14))
        '−4:14'
        >>> delta(timedelta())
        '0:00'
        >>> delta(timedelta(seconds=-30))
        '0:00'
    """
    if is_level(value):
        return ZERO
    return f"{'+' if value > timedelta() else MINUS}{hm(value)}"


def digits(value: timedelta) -> str:
    """A signed duration for Textual's ``Digits``, whose glyph set has no U+2212.

    Examples:
        >>> digits(timedelta(hours=-10, minutes=-50))
        '-10:50'
        >>> digits(timedelta(hours=12, minutes=40))
        '+12:40'
        >>> digits(timedelta(seconds=-30))
        '0:00'
    """
    if is_level(value):
        return ZERO
    return f"{'+' if value > timedelta() else '-'}{hm(value)}"


_CONTROL_CATEGORIES = frozenset({"Cc", "Cf"})
"""Unicode categories a terminal reads as instructions, not as text."""


def printable(text: str) -> str:
    """A label with the characters a terminal would obey taken out of it.

    Bank holiday titles come from GOV.UK and absence notes from whatever was
    pasted into a field, and both are drawn straight at a screen, where an ESC,
    a BEL or a carriage return recolours the terminal, retitles the window or
    fakes a line of output. Click strips only the CSI form.

    Examples:
        >>> printable("Boxing Day" + chr(27) + "[31m" + chr(13))
        'Boxing Day[31m'
    """
    return "".join(
        character
        for character in text
        if unicodedata.category(character) not in _CONTROL_CATEGORIES
    )


def stamp(when: date, pattern: str) -> str:
    """``strftime`` with an unpadded day, on every platform.

    ``%-d`` is a glibc and BSD extension and raises on Windows, so the day is
    substituted before ``strftime`` sees it.

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


def month_title(year: int, month: int) -> str:
    """A month named in full, with its year.

    Examples:
        >>> month_title(2026, 6)
        'June 2026'
    """
    return f"{date(year, month, 1):%B %Y}"


def clock(moment: datetime) -> str:
    """A wall-clock time.

    Examples:
        >>> clock(datetime(2026, 6, 11, 9, 12))
        '09:12'
    """
    return moment.strftime("%H:%M")


def spoken(span: timedelta) -> str:
    """A short duration as it is said out loud.

    Whole minutes where it divides, seconds otherwise, so a threshold reads as
    "a minute" and not as "60 seconds".

    Examples:
        >>> spoken(timedelta(minutes=1))
        '1 minute'
        >>> spoken(timedelta(minutes=5))
        '5 minutes'
        >>> spoken(timedelta(seconds=90))
        '90 seconds'
        >>> spoken(timedelta(seconds=1))
        '1 second'
    """
    seconds = int(span.total_seconds())
    minutes, remainder = divmod(seconds, SECONDS_PER_MINUTE)
    if minutes and not remainder:
        return f"{minutes} {plural(minutes, 'minute')}"
    return f"{seconds} {plural(seconds, 'second')}"


def days(value: float) -> str:
    """A count of days, with a half only when there is one.

    Examples:
        >>> days(18.5)
        '18.5'
        >>> days(19.0)
        '19'
    """
    return f"{value:g}"


def plural(count: float, noun: str) -> str:
    """``noun`` agreeing with ``count``, so nothing reads "1 days".

    A half is plural: half a day is not one of anything.

    Examples:
        >>> plural(1, "day")
        'day'
        >>> plural(0.5, "day")
        'days'
        >>> plural(2, "bank holiday")
        'bank holidays'
    """
    return noun if count == 1 else f"{noun}s"


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
