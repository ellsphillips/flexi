"""Reading, and shifting, the several ways a date can be typed.

In the domain, not beside the modal that reads one, because a command line
needs the same vocabulary a dialog does and the CLI cannot import Textual.

Everything is read relative to ``reference``, which is often not today:
:func:`parse_span` reads a written end from the range's *start*, and both
modals read from the day on screen. :class:`Preference` settles what a date
with no year, or no month, means; nothing else about the grammar changes.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping
from datetime import date, timedelta
from types import MappingProxyType
from typing import Final

from flexi.domain import leaveyear
from flexi.domain.format import stamp

__all__ = (
    "DATE_HELP",
    "DATE_RANGE_ERROR",
    "DAYS_IN_WEEK",
    "DAY_NAMES",
    "FORMATS",
    "LEAP_SENTINEL_YEAR",
    "MONTHS_IN_YEAR",
    "MONTH_NAMES",
    "OFFSET_UNITS",
    "RELATIVE_DAYS",
    "SEPARATORS",
    "SHORTEST_DAY_NAME",
    "SUPPORTED_FIRST",
    "SUPPORTED_LAST",
    "Preference",
    "add_days",
    "add_months",
    "days_between",
    "forward_if_passed",
    "month_index",
    "parse_date",
    "parse_day_of_month",
    "parse_offset",
    "parse_span",
    "parse_weekday",
    "parse_written",
    "relative_to",
    "resolve_month_day",
    "week_start",
    "weekday_index",
)

DAYS_IN_WEEK: Final = 7
MONTHS_IN_YEAR: Final = 12
LEAP_SENTINEL_YEAR: Final = 2000
"""A leap year used only to validate a month-and-day pair."""

MONTH_NAMES: Final[tuple[str, ...]] = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
"""English month names, independent of the process locale."""

DAY_NAMES: Final[tuple[str, ...]] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
"""The week, in order.

Written out, not taken from `calendar.day_name`, which follows the locale: a
working pattern is stored as words, and reading it back under another locale
must not change the answer.
"""

SHORTEST_DAY_NAME: Final = 3
"""Mon, Tue, Wed. One letter cannot tell Tue from Thu, or Sat from Sun."""

OFFSET_UNITS: Final[Mapping[str, int]] = MappingProxyType({"d": 1, "w": 7})

RELATIVE_DAYS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "today": 0,
        "tomorrow": 1,
        "yesterday": -1,
        "next week": DAYS_IN_WEEK,
        "last week": -DAYS_IN_WEEK,
    }
)
"""Dates named by their distance from the reference day, in words."""

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

SUPPORTED_FIRST: Final = date(date.min.year + 1, 1, 1)
SUPPORTED_LAST: Final = date(date.max.year - 1, 12, 31)
"""The window a typed date is read into.

A leave year reaches into the calendar year on either side of the date it
holds, so the first and the last year the calendar has cannot be worked in.
`leaveyear.bounds(date(9999, 12, 31), 4, 6)` asks `date` for the year 10000.
"""

DATE_RANGE_ERROR: Final = (
    f"Date falls outside {SUPPORTED_FIRST.isoformat()} to {SUPPORTED_LAST.isoformat()}"
)


class Preference(enum.Enum):
    """How a date with no year, or no month, is resolved."""

    CURRENT = "current"
    """This month, this year. What "go to date" means."""

    FORWARD = "forward"
    """The next such date on or after the reference. What booking leave means."""


def parse_date(
    raw: str, *, reference: date, prefer: Preference = Preference.CURRENT
) -> date:
    """Read a date in any of the forms Flexi accepts.

    Accepts ``2026-06-12``, ``12 Jun``, ``jun 12``, ``12/06``, a bare ``12``,
    ``today``/``tomorrow``/``yesterday``, a weekday name meaning the next such
    day, ``next friday``, ``last friday``, ``next week``, and offsets like
    ``+3d`` or ``-2w``. Anything else raises a ``ValueError`` naming the forms
    it understands.

    Examples:
        >>> parse_date("friday", reference=date(2026, 8, 10))
        datetime.date(2026, 8, 14)
        >>> parse_date("next monday", reference=date(2026, 8, 10))
        datetime.date(2026, 8, 17)
        >>> parse_date("tomorrow", reference=date(2026, 8, 10))
        datetime.date(2026, 8, 11)
    """
    text = " ".join(raw.strip().lower().split())
    if not text:
        msg = "Type a date, a day of the month, or an offset like +3d"
        raise ValueError(msg)

    # A date is always truthy, so the first reader to answer wins and the rest
    # are never called. Each reader converts its own `OverflowError`, so the
    # range error a caller sees comes from the reader that computed the date.
    found = (
        relative_to(text, reference)
        or parse_weekday(text, reference)
        or parse_offset(text, reference)
        or parse_written(text, reference, prefer)
        or parse_day_of_month(text, reference, prefer)
    )
    if found is None:
        raise ValueError(DATE_HELP)
    return _supported(found)


def parse_span(
    raw: str, *, reference: date, prefer: Preference = Preference.FORWARD
) -> tuple[date, date]:
    """A date, or a pair separated by ``to``, ``until``, ``through`` or ``..``.

    A written end is read *from the start*, not from the reference, so
    ``28 dec to 4 jan`` lands in the following year and ``monday to friday``
    stays in one week. A word and an offset still name a day from the
    reference: the end of ``last week to today`` is today, and ``+1d to +3d``
    is three days from now, not four.

    Examples:
        >>> parse_span("monday to friday", reference=date(2026, 8, 10))
        (datetime.date(2026, 8, 10), datetime.date(2026, 8, 14))
        >>> parse_span("last week to today", reference=date(2026, 8, 10))
        (datetime.date(2026, 8, 3), datetime.date(2026, 8, 10))
        >>> parse_span("12 jun", reference=date(2026, 8, 10))
        (datetime.date(2027, 6, 12), datetime.date(2027, 6, 12))
    """
    text = " ".join(raw.strip().lower().split())
    for separator in SEPARATORS:
        if separator in text:
            head, _, tail = text.partition(separator)
            start = parse_date(head, reference=reference, prefer=prefer)
            end = _supported(
                relative_to(tail, reference)
                or parse_offset(tail, reference)
                or parse_date(tail, reference=start, prefer=Preference.FORWARD)
            )
            if end < start:
                # Through `stamp`, not `{end:%-d %b %Y}`: `%-d` is a glibc and
                # BSD extension, and Windows `strftime` raises `ValueError:
                # Invalid format string` on it.
                msg = (
                    f"That range runs backwards: {stamp(end, '%-d %b %Y')} "
                    f"is before {stamp(start, '%-d %b %Y')}"
                )
                raise ValueError(msg)
            return start, end

    only = parse_date(text, reference=reference, prefer=prefer)
    return only, only


# The readers, tried in order ------------------------------------------------


def relative_to(text: str, reference: date) -> date | None:
    """A date named by how far it is from the reference day, in words.

    Examples:
        >>> relative_to("tomorrow", date(2026, 8, 10))
        datetime.date(2026, 8, 11)
        >>> relative_to("last week", date(2026, 8, 10))
        datetime.date(2026, 8, 3)
        >>> relative_to("a week on tuesday", date(2026, 8, 10)) is None
        True
    """
    offset = RELATIVE_DAYS.get(text)
    return None if offset is None else add_days(reference, offset)


def weekday_index(name: str) -> int | None:
    """A weekday by name, however far it was abbreviated, or ``None``.

    Names only. A bare number is a day of the month everywhere else in this
    grammar, and reading ``0`` as Monday would take ``flexi leave annual 3``
    from the third of the month to Thursday.

    Examples:
        >>> weekday_index("Friday")
        4
        >>> weekday_index("thurs")
        3
        >>> weekday_index("th") is None
        True
        >>> weekday_index("junuary") is None
        True
    """
    token = name.strip().lower()
    if len(token) < SHORTEST_DAY_NAME:
        return None
    return next(
        (index for index, day in enumerate(DAY_NAMES) if day.startswith(token)), None
    )


def month_index(name: str) -> int | None:
    """An English month number, from its full name or three-letter form.

    The names are Flexi's own data, not ``strptime`` state owned by the process
    locale, so a date typed in English means the same on every machine.

    Examples:
        >>> month_index("June")
        6
        >>> month_index("sep")
        9
        >>> month_index("juin") is None
        True
    """
    token = name.strip().lower()
    return next(
        (
            index
            for index, month in enumerate(MONTH_NAMES, start=1)
            if token == month or token == month[:3]
        ),
        None,
    )


def parse_weekday(text: str, reference: date) -> date | None:
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
    leading = weekday_index(rest)
    if word in {"next", "last"} and leading is not None:
        target = leading
        if word == "next":
            # Never the same day: "next friday" on a Friday means the one coming.
            ahead = (target - reference.weekday()) % DAYS_IN_WEEK
            return add_days(reference, ahead or DAYS_IN_WEEK)
        # And "last friday" said on a Friday means the one just gone.
        behind = (reference.weekday() - target) % DAYS_IN_WEEK
        return add_days(reference, -(behind or DAYS_IN_WEEK))

    bare = weekday_index(text)
    if bare is not None:
        # A bare weekday is the next one, and the reference day counts as itself.
        return add_days(reference, (bare - reference.weekday()) % DAYS_IN_WEEK)
    return None


def parse_offset(text: str, reference: date) -> date | None:
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
        return add_days(reference, count * OFFSET_UNITS[unit])
    if unit == "m":
        return add_months(reference, count)
    return add_months(reference, count * MONTHS_IN_YEAR)


def parse_written(
    text: str, reference: date, prefer: Preference = Preference.CURRENT
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

    numeric = re.fullmatch(
        r"(?P<day>\d{1,2})/(?P<month>\d{1,2})(?:/(?P<year>\d{4}))?", text
    ) or re.fullmatch(r"(?P<day>\d{1,2})-(?P<month>\d{1,2})-(?P<year>\d{4})", text)
    if numeric is not None:
        day = int(numeric["day"])
        month = int(numeric["month"])
        year = int(numeric["year"]) if numeric["year"] is not None else None
    else:
        year_text: str | None
        match text.split():
            case [first, second]:
                year_text = None
            case [first, second, candidate] if re.fullmatch(r"\d{4}", candidate):
                year_text = candidate
            case _:
                return None
        first_month = month_index(first)
        second_month = month_index(second)
        if first_month is not None and second.isdecimal():
            day, month = int(second), first_month
        elif first.isdecimal() and second_month is not None:
            day, month = int(first), second_month
        else:
            return None
        year = int(year_text) if year_text is not None else None

    if year is not None:
        try:
            return date(year, month, day)
        except (OverflowError, ValueError):
            return None
    return resolve_month_day(month, day, reference, prefer)


def parse_day_of_month(
    text: str, reference: date, prefer: Preference = Preference.CURRENT
) -> date | None:
    """A bare day number: this month, or the next month that has one.

    Booking forwards, the answer is the next date carrying that day number,
    which is not always next month: the 30th asked for in February is the 30th
    of March.

    A number no month has raises instead of returning ``None``. This is the
    last reader in the chain, so nothing after it can report the typo.

    Examples:
        >>> parse_day_of_month("12", date(2026, 8, 10))
        datetime.date(2026, 8, 12)
        >>> parse_day_of_month("3", date(2026, 8, 10), Preference.FORWARD)
        datetime.date(2026, 9, 3)
        >>> parse_day_of_month("30", date(2026, 2, 10), Preference.FORWARD)
        datetime.date(2026, 3, 30)
        >>> parse_day_of_month("30", date(2026, 1, 31), Preference.FORWARD)
        datetime.date(2026, 3, 30)
        >>> parse_day_of_month("friday", date(2026, 8, 10)) is None
        True
    """
    if not text.isdecimal():
        return None
    day = int(text)
    if prefer is Preference.FORWARD:
        # Walked from the first of this month, so `add_months` can never clamp
        # the cursor itself. `clamp` shortens the candidate, and a candidate
        # that came back shorter is a month that does not have this day.
        month = reference.replace(day=1)
        for _ in range(MONTHS_IN_YEAR):
            candidate = leaveyear.clamp(month.year, month.month, day)
            if candidate.day == day and candidate >= reference:
                return candidate
            month = add_months(month, 1)
    try:
        return reference.replace(day=day)
    except (OverflowError, ValueError) as error:
        msg = f"{MONTH_NAMES[reference.month - 1].title()} has no day {day}"
        raise ValueError(msg) from error


# Arithmetic ------------------------------------------------------------------


def add_months(when: date, count: int) -> date:
    """Move whole months, clamping to the end of a shorter one.

    Clamping is :func:`flexi.domain.leaveyear.clamp`: the 31st of a month has
    no counterpart in the next, and neither does the 29th of February in three
    years out of four.

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
    try:
        return leaveyear.clamp(year, month + 1, when.day)
    except (OverflowError, ValueError) as error:
        raise ValueError(DATE_RANGE_ERROR) from error


def add_days(when: date, count: int) -> date:
    """Move a date by whole days, or raise the supported-range refusal.

    ``timedelta`` and date addition both raise ``OverflowError`` for an
    enormous offset. Normalising it to ``ValueError`` here keeps every public
    reader on the one failure the input boundaries catch.

    Examples:
        >>> add_days(date(2026, 8, 10), 3)
        datetime.date(2026, 8, 13)
    """
    try:
        return when + timedelta(days=count)
    except OverflowError as error:
        raise ValueError(DATE_RANGE_ERROR) from error


def week_start(when: date, *, first_weekday: int) -> date:
    """The first day of the week ``when`` falls in.

    ``first_weekday`` is required, and keyword-only: it is a configured
    setting, and a default here would let a caller quietly assume Monday.

    Examples:
        >>> week_start(date(2026, 6, 11), first_weekday=0)
        datetime.date(2026, 6, 8)
        >>> week_start(date(2026, 6, 11), first_weekday=6)
        datetime.date(2026, 6, 7)
        >>> week_start(date(2026, 6, 8), first_weekday=0)
        datetime.date(2026, 6, 8)
    """
    return add_days(when, -((when.weekday() - first_weekday) % DAYS_IN_WEEK))


def days_between(start: date, end: date) -> list[date]:
    """Every date from ``start`` to ``end``, inclusive.

    Empty when ``end`` falls before ``start``, which is how many dates there
    are between them. Callers refuse a backwards span before reaching here.

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


# Helpers --------------------------------------------------------------------


def _supported(found: date) -> date:
    """A date the rest of Flexi can work with, or the range refusal.

    The calendar runs from year 1 to year 9999 and leave-year arithmetic reads
    a year either side of the date it is given, so the two end years are dates
    `date` accepts and Flexi cannot hold. This is the boundary every typed date
    crosses, and its callers turn a ``ValueError`` into a message.
    """
    if not SUPPORTED_FIRST <= found <= SUPPORTED_LAST:
        raise ValueError(DATE_RANGE_ERROR)
    return found


def forward_if_passed(
    parsed: date, reference: date, prefer: Preference = Preference.CURRENT
) -> date:
    """A date already read in this year, moved on if booking has passed it.

    Examples:
        >>> forward_if_passed(date(2026, 6, 12), date(2026, 8, 10), Preference.CURRENT)
        datetime.date(2026, 6, 12)
        >>> forward_if_passed(date(2026, 6, 12), date(2026, 8, 10), Preference.FORWARD)
        datetime.date(2027, 6, 12)
    """
    if prefer is Preference.FORWARD and parsed < reference:
        return resolve_month_day(
            parsed.month, parsed.day, reference, Preference.FORWARD
        )
    return parsed


def resolve_month_day(
    month: int,
    day: int,
    reference: date,
    prefer: Preference = Preference.CURRENT,
) -> date:
    """Resolve an exact month and day against a reference date.

    ``CURRENT`` uses the reference year. ``FORWARD`` finds the next exact
    occurrence, never clamping an explicit ``29 February`` to the 28th.

    Examples:
        >>> resolve_month_day(2, 29, date(2026, 8, 10), Preference.FORWARD)
        datetime.date(2028, 2, 29)
        >>> resolve_month_day(2, 29, date(2028, 3, 1), Preference.FORWARD)
        datetime.date(2032, 2, 29)
    """
    try:
        date(LEAP_SENTINEL_YEAR, month, day)
    except (OverflowError, ValueError) as error:
        msg = f"{day}/{month} is not a valid calendar day"
        raise ValueError(msg) from error

    if prefer is Preference.CURRENT:
        try:
            return date(reference.year, month, day)
        except ValueError as error:
            msg = f"{MONTH_NAMES[month - 1].title()} {reference.year} has no day {day}"
            raise ValueError(msg) from error

    for year in range(reference.year, date.max.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= reference:
            return candidate

    raise ValueError(DATE_RANGE_ERROR)
