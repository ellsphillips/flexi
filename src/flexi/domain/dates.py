"""Reading the several ways somebody might type a date, and moving between them.

Lives in the domain rather than beside the modal that used to own it, because a
command line needs the same vocabulary a dialog does, and the CLI cannot import
Textual.

A dialog and a command line want different defaults, though. Somebody typing
``12`` into "go to date" while looking at June means the 12th of June, whether
or not it has passed. Somebody typing ``flexi leave annual 12`` is booking
leave, and leave is booked forwards. That is what :class:`Preference` chooses
between; nothing else about the grammar changes.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Callable
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

SEPARATORS: Final[tuple[str, ...]] = (" to ", " until ", " through ", "..")

FORMATS: Final[tuple[str, ...]] = (
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

DATE_HELP: Final = "Try 2026-06-12, 12 Jun, friday, next monday, tomorrow, 12, or +3d"


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

    for reader in READERS:
        found = reader(text, today, prefer)
        if found is not None:
            return found

    raise ValueError(DATE_HELP)


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


type Reader = Callable[[str, date, Preference], date | None]
"""One way of reading a date, answering ``None`` when the text is not its shape.

The three that do not resolve an ambiguous date take ``prefer`` and discard it.
That is the price of one signature: `parse_date` walks :data:`READERS` in order
and the first answer wins, so a reader has to be interchangeable with its
neighbours whether or not it needs everything it is handed.
"""


def parse_relative(text: str, today: date, prefer: Preference) -> date | None:
    del prefer
    if text == "today":
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text == "yesterday":
        return today - timedelta(days=1)
    if text == "next week":
        return today + timedelta(days=DAYS_IN_WEEK)
    if text == "last week":
        return today - timedelta(days=DAYS_IN_WEEK)
    return None


def parse_weekday(text: str, today: date, prefer: Preference) -> date | None:
    """A weekday name, optionally with ``next`` or ``last`` in front of it."""
    del prefer
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


def parse_offset(text: str, today: date, prefer: Preference) -> date | None:
    del prefer
    if not re.fullmatch(r"[+-]\d+[dwmy]", text):
        return None
    unit, count = text[-1], int(text[:-1])
    if unit in OFFSET_UNITS:
        return today + timedelta(days=count * OFFSET_UNITS[unit])
    if unit == "m":
        return add_months(today, count)
    return add_months(today, count * MONTHS_IN_YEAR)


def parse_written(text: str, today: date, prefer: Preference) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    for pattern in FORMATS:
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


def parse_day_of_month(text: str, today: date, prefer: Preference) -> date | None:
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


# -- helpers -----------------------------------------------------------------


def forward_if_passed(parsed: date, today: date, prefer: Preference) -> date:
    """A date already read in this year, moved on if booking has passed it."""
    if prefer is Preference.FORWARD and parsed < today:
        return add_months(parsed, MONTHS_IN_YEAR)
    return parsed


def date_range(start: date, end: date) -> list[date]:
    """Every date from ``start`` to ``end``, inclusive; empty if they cross.

    `services.ledger` and `services.absence` each carried this, byte for byte,
    under a different name.

    Examples:
        >>> [f"{day:%d}" for day in date_range(date(2026, 6, 10), date(2026, 6, 12))]
        ['10', '11', '12']
        >>> date_range(date(2026, 6, 12), date(2026, 6, 10))
        []
    """
    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(max(0, span + 1))]


def add_months(when: date, count: int) -> date:
    """Move whole months, clamping to the end of a shorter one.

    The one implementation. `domain.period` had a second and
    `components.modules.monthview` a third, and the day-length arithmetic under
    it was a hand-rolled `_days_in` that `calendar.monthrange` already answers
    -- which `leaveyear.clamp` was already calling.

    Examples:
        >>> add_months(date(2026, 1, 31), 1)
        datetime.date(2026, 2, 28)
        >>> add_months(date(2026, 3, 15), -2)
        datetime.date(2026, 1, 15)
    """
    total = when.year * MONTHS_IN_YEAR + (when.month - 1) + count
    year, month = divmod(total, MONTHS_IN_YEAR)
    return leaveyear.clamp(year, month + 1, when.day)


READERS: Final[tuple[Reader, ...]] = (
    parse_relative,
    parse_weekday,
    parse_offset,
    parse_written,
    parse_day_of_month,
)
"""Every way a date can be read, in the order `parse_date` tries them.

Order is the grammar: a bare ``12`` is a day of the month only because nothing
before it claimed the text.
"""
