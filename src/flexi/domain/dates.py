"""Reading, and shifting, the several ways somebody might type a date.

Lives in the domain rather than beside the modal that used to own it, because a
command line needs the same vocabulary a dialog does, and the CLI cannot import
Textual.

A dialog and a command line want different defaults, though. Somebody typing
``12`` into "go to date" while looking at June means the 12th of June, whether
or not it has passed. Somebody typing ``flexi leave annual 12`` is booking
leave, and leave is booked forwards. That is what :class:`Preference` chooses
between; nothing else about the grammar changes.

:func:`add_months` and :func:`days_between` are public for the same reason
``leaveyear`` is: they are the two questions every layer asks about a span, and
answering them privately here left four other answers scattered across the
widgets and the services.
"""

from __future__ import annotations

import enum
import re
from datetime import date, datetime, timedelta
from typing import Final

from flexi.domain import leaveyear
from flexi.domain.format import stamp

DAYS_IN_WEEK: Final = 7
MONTHS_IN_YEAR: Final = 12

WEEKDAYS: Final[dict[str, int]] = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

OFFSET_UNITS: Final[dict[str, int]] = {"d": 1, "w": 7}

RELATIVE_DAYS: Final[dict[str, int]] = {
    "today": 0,
    "tomorrow": 1,
    "yesterday": -1,
    "next week": DAYS_IN_WEEK,
    "last week": -DAYS_IN_WEEK,
}
"""Dates named by their distance from today, in words."""

SEPARATORS: Final[tuple[str, ...]] = (" to ", " until ", " through ", "..")

_FORMATS: Final[tuple[str, ...]] = (
    "%d %b %Y",
    "%d %B %Y",
    "%d %b",
    "%d %B",
    "%b %d %Y",
    "%B %d %Y",
    "%b %d",
    "%B %d",
    "%d/%m/%Y",
    "%d/%m",
    "%d-%m-%Y",
)

_HELP: Final = "Try 2026-06-12, 12 Jun, friday, next monday, tomorrow, 12, or +3d"


class Preference(enum.Enum):
    """How a date with no year, or no month, is resolved."""

    CURRENT = "current"
    """This month, this year. What "go to date" means."""

    FORWARD = "forward"
    """The next such date on or after today. What booking leave means."""


def parse_date(
    raw: str, *, today: date, prefer: Preference = Preference.CURRENT
) -> date:
    """Read the several ways somebody might type a date.

    Accepts ``2026-06-12``, ``12 Jun``, ``jun 12``, ``12/06``, a bare ``12``,
    ``today``/``tomorrow``/``yesterday``, a weekday name meaning the next such
    day, ``next friday``, ``last friday``, ``next week``, and offsets like
    ``+3d`` or ``-2w``.

    Raises ``ValueError`` naming the forms it understands, because "invalid
    date" tells nobody anything.

    Examples:
        >>> parse_date("friday", today=date(2026, 8, 10))
        datetime.date(2026, 8, 14)
        >>> parse_date("next monday", today=date(2026, 8, 10))
        datetime.date(2026, 8, 17)
        >>> parse_date("tomorrow", today=date(2026, 8, 10))
        datetime.date(2026, 8, 11)
    """
    text = " ".join(raw.strip().lower().split())
    if not text:
        msg = "Type a date, a day of the month, or an offset like +3d"
        raise ValueError(msg)

    # `or` rather than a table of readers: a date is always truthy, so the
    # first that answers wins and the rest are never called -- and each reader
    # keeps the signature it deserves instead of a uniform one that had three
    # of them `del` an argument they never read.
    found = (
        relative_to(text, today)
        or parse_weekday(text, today)
        or parse_offset(text, today)
        or parse_written(text, today, prefer)
        or parse_day_of_month(text, today, prefer)
    )
    if found is None:
        raise ValueError(_HELP)
    return found


def parse_span(
    raw: str, *, today: date, prefer: Preference = Preference.FORWARD
) -> tuple[date, date]:
    """A date, or a pair separated by ``to``, ``until``, ``through`` or ``..``.

    The end is read *from the start* rather than from today, so ``28 dec to
    4 jan`` lands in the following year and ``monday to friday`` stays in one
    week.

    Examples:
        >>> parse_span("monday to friday", today=date(2026, 8, 10))
        (datetime.date(2026, 8, 10), datetime.date(2026, 8, 14))
        >>> parse_span("12 jun", today=date(2026, 8, 10))
        (datetime.date(2027, 6, 12), datetime.date(2027, 6, 12))
    """
    text = " ".join(raw.strip().lower().split())
    for separator in SEPARATORS:
        if separator in text:
            head, _, tail = text.partition(separator)
            start = parse_date(head, today=today, prefer=prefer)
            end = parse_date(tail, today=start, prefer=Preference.FORWARD)
            if end < start:
                # Through `stamp`, not `{end:%-d %b %Y}`. `%-d` is a glibc and
                # BSD extension: on Windows `strftime` raises `ValueError:
                # Invalid format string`, so the message explaining a typo was
                # itself a crash, and the one place it could happen was the
                # line reporting somebody else's mistake.
                msg = (
                    f"That range runs backwards: {stamp(end, '%-d %b %Y')} "
                    f"is before {stamp(start, '%-d %b %Y')}"
                )
                raise ValueError(msg)
            return start, end

    only = parse_date(text, today=today, prefer=prefer)
    return only, only


# -- the readers, tried in order --------------------------------------------


def relative_to(text: str, today: date) -> date | None:
    """A date named by how far it is from today, in words.

    A table rather than a ladder of five comparisons: the vocabulary is data,
    and this way it can be read -- and extended -- without reading code.

    Examples:
        >>> relative_to("tomorrow", date(2026, 8, 10))
        datetime.date(2026, 8, 11)
        >>> relative_to("last week", date(2026, 8, 10))
        datetime.date(2026, 8, 3)
        >>> relative_to("a week on tuesday", date(2026, 8, 10)) is None
        True
    """
    offset = RELATIVE_DAYS.get(text)
    return None if offset is None else today + timedelta(days=offset)


def parse_weekday(text: str, today: date) -> date | None:
    """A weekday name, optionally with ``next`` or ``last`` in front of it.

    Examples:
        >>> parse_weekday("friday", date(2026, 8, 10))
        datetime.date(2026, 8, 14)
        >>> parse_weekday("last friday", date(2026, 8, 14))
        datetime.date(2026, 8, 7)
        >>> parse_weekday("12 jun", date(2026, 8, 10)) is None
        True
    """
    word, _, rest = text.partition(" ")
    if word in {"next", "last"} and rest in WEEKDAYS:
        target = WEEKDAYS[rest]
        if word == "next":
            # Never today: "next friday" said on a Friday means the one coming.
            ahead = (target - today.weekday()) % DAYS_IN_WEEK
            return today + timedelta(days=ahead or DAYS_IN_WEEK)
        # And "last friday" said on a Friday means the one just gone.
        behind = (today.weekday() - target) % DAYS_IN_WEEK
        return today - timedelta(days=behind or DAYS_IN_WEEK)

    if text in WEEKDAYS:
        # A bare weekday is the next one, and today counts as itself.
        ahead = (WEEKDAYS[text] - today.weekday()) % DAYS_IN_WEEK
        return today + timedelta(days=ahead)
    return None


def parse_offset(text: str, today: date) -> date | None:
    """A signed step in days, weeks, months or years, like ``+3d`` or ``-2w``.

    Examples:
        >>> parse_offset("+3d", date(2026, 8, 10))
        datetime.date(2026, 8, 13)
        >>> parse_offset("-2w", date(2026, 8, 10))
        datetime.date(2026, 7, 27)
        >>> parse_offset("+1y", date(2026, 8, 10))
        datetime.date(2027, 8, 10)
        >>> parse_offset("3d", date(2026, 8, 10)) is None
        True
    """
    if not re.fullmatch(r"[+-]\d+[dwmy]", text):
        return None
    unit, count = text[-1], int(text[:-1])
    if unit in OFFSET_UNITS:
        return today + timedelta(days=count * OFFSET_UNITS[unit])
    if unit == "m":
        return add_months(today, count)
    return add_months(today, count * MONTHS_IN_YEAR)


def parse_written(
    text: str, today: date, prefer: Preference = Preference.CURRENT
) -> date | None:
    """A date written out: ISO, ``12 Jun``, ``jun 12``, ``12/06``.

    Examples:
        >>> parse_written("2026-06-12", date(2026, 8, 10))
        datetime.date(2026, 6, 12)
        >>> parse_written("12 jun", date(2026, 8, 10), Preference.FORWARD)
        datetime.date(2027, 6, 12)
        >>> parse_written("friday", date(2026, 8, 10)) is None
        True
    """
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    for pattern in _FORMATS:
        dated, dated_pattern = (
            (text, pattern)
            if "%Y" in pattern
            # A year-less pattern defaults to 1900, which is not a leap year, so
            # "29 feb" raises rather than parsing. Python 3.15 changes the
            # default besides. Supplying a year takes the question away.
            else (f"{text} {today.year}", f"{pattern} %Y")
        )
        try:
            parsed = datetime.strptime(dated, dated_pattern).date()  # noqa: DTZ007
        except ValueError:
            continue
        if "%Y" in pattern:
            return parsed
        return forward_if_passed(parsed, today, prefer)
    return None


def parse_day_of_month(
    text: str, today: date, prefer: Preference = Preference.CURRENT
) -> date | None:
    """A bare day number, in this month or the next.

    Raises rather than returning ``None`` for a number no month has: ``32`` is
    a typo, and the last reader in the chain is the only one that can tell.

    Examples:
        >>> parse_day_of_month("12", date(2026, 8, 10))
        datetime.date(2026, 8, 12)
        >>> parse_day_of_month("3", date(2026, 8, 10), Preference.FORWARD)
        datetime.date(2026, 9, 3)
        >>> parse_day_of_month("friday", date(2026, 8, 10)) is None
        True
    """
    if not text.isdigit():
        return None
    day = int(text)
    try:
        this_month = today.replace(day=day)
    except ValueError as error:
        msg = f"{today:%B} has no day {day}"
        raise ValueError(msg) from error
    if prefer is Preference.FORWARD and this_month < today:
        return add_months(this_month, 1)
    return this_month


# -- arithmetic ---------------------------------------------------------------


def add_months(when: date, count: int) -> date:
    """Move whole months, clamping to the end of a shorter one.

    Clamping is :func:`flexi.domain.leaveyear.clamp`, which is where the reason
    for it is written down: the 31st of a month has no counterpart in the next,
    and neither does the 29th of February in three years out of four.

    Examples:
        >>> add_months(date(2026, 1, 31), 1)
        datetime.date(2026, 2, 28)
        >>> add_months(date(2026, 3, 15), -2)
        datetime.date(2026, 1, 15)
        >>> add_months(date(2026, 12, 1), 1)
        datetime.date(2027, 1, 1)
    """
    total = when.year * MONTHS_IN_YEAR + (when.month - 1) + count
    year, month = divmod(total, MONTHS_IN_YEAR)
    return leaveyear.clamp(year, month + 1, when.day)


def week_start(when: date, first_weekday: int = 0) -> date:
    """The first day of the week ``when`` falls in.

    One question with one answer. It was worked out in five places, and two of
    them -- the dashboard's month grid and its column headings -- assumed Monday
    while the period beside them honoured the configured first day, so a
    Sunday-first week tinted two rows of a Monday-first grid.

    Examples:
        >>> week_start(date(2026, 6, 11))
        datetime.date(2026, 6, 8)
        >>> week_start(date(2026, 6, 11), first_weekday=6)
        datetime.date(2026, 6, 7)
        >>> week_start(date(2026, 6, 8))
        datetime.date(2026, 6, 8)
    """
    return when - timedelta(days=(when.weekday() - first_weekday) % DAYS_IN_WEEK)


def days_between(start: date, end: date) -> list[date]:
    """Every date from ``start`` to ``end``, inclusive.

    Empty when ``end`` falls before ``start``, because that is how many dates
    there are between them. Callers refuse a backwards span before they get
    here -- ``parse_span`` raises and the leave screen will not build one -- so
    this is the answer to a question nobody should be asking, not a fallback
    anybody relies on.

    Examples:
        >>> days_between(date(2026, 6, 1), date(2026, 6, 3))[-1]
        datetime.date(2026, 6, 3)
        >>> len(days_between(date(2026, 6, 1), date(2026, 6, 3)))
        3
        >>> days_between(date(2026, 6, 1), date(2026, 6, 1))
        [datetime.date(2026, 6, 1)]
        >>> days_between(date(2026, 6, 3), date(2026, 6, 1))
        []
    """
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


# -- helpers -----------------------------------------------------------------


def forward_if_passed(
    parsed: date, today: date, prefer: Preference = Preference.CURRENT
) -> date:
    """A date already read in this year, moved on if booking has passed it.

    Examples:
        >>> forward_if_passed(date(2026, 6, 12), date(2026, 8, 10), Preference.CURRENT)
        datetime.date(2026, 6, 12)
        >>> forward_if_passed(date(2026, 6, 12), date(2026, 8, 10), Preference.FORWARD)
        datetime.date(2027, 6, 12)
    """
    if prefer is Preference.FORWARD and parsed < today:
        return add_months(parsed, MONTHS_IN_YEAR)
    return parsed
