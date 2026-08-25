"""Enumerations shared across every layer.

Nothing here imports anything else in Flexi, so it can be imported from the
domain, the services and the widgets without a cycle.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from enum import StrEnum


class EventSource(StrEnum):
    """Who punched the clock.

    Two values, written out as bare strings in six places and typed `str` on
    the two service methods that take one -- the same closed vocabulary
    `Division` is an enum for, and for the same reason: migration 0010 tells
    the two apart to decide whose timestamps it may rewrite, so a typo here is
    a silent data conversion rather than an error.
    """

    USER = "user"
    """Somebody pressed a key."""

    SYSTEM = "system"
    """Flexi closed a session nobody closed."""


class Division(StrEnum):
    """A GOV.UK bank holiday division.

    The three are a closed vocabulary and every other closed vocabulary in this
    module is an enum, but this one was a bare string in nine places across six
    files -- including a default argument that silently gave two of the three
    regions the wrong calendar. A `StrEnum` keeps the stored value a string, so
    nothing about the database or the GOV.UK payload changes.
    """

    ENGLAND_AND_WALES = "england-and-wales"
    SCOTLAND = "scotland"
    NORTHERN_IRELAND = "northern-ireland"

    @property
    def label(self) -> str:
        """The name shown to a reader."""
        return _DIVISION_LABELS[self]

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Label and value, for a Select or a click.Choice."""
        return [(member.label, member.value) for member in cls]


_DIVISION_LABELS: dict[Division, str] = {
    Division.ENGLAND_AND_WALES: "England & Wales",
    Division.SCOTLAND: "Scotland",
    Division.NORTHERN_IRELAND: "Northern Ireland",
}

DEFAULT_DIVISION = Division.ENGLAND_AND_WALES
"""What to assume before anybody has chosen. Named, so the assumption is
visible wherever it is made rather than spelled out as a literal."""


class ClockAction(enum.Enum):
    """Clock action type persisted to the database."""

    IN = "in"
    OUT = "out"


class AbsenceType(enum.Enum):
    """A reason a working day was not worked.

    A bank holiday is deliberately absent: it is a property of the date rather
    than something a person books, it comes from GOV.UK, and it cannot be
    created or removed from the interface.
    """

    ANNUAL = "annual"
    SICK = "sick"
    FLEXI = "flexi"
    UNPAID = "unpaid"
    OTHER = "other"

    @property
    def label(self) -> str:
        """The name shown to a reader."""
        return _DETAILS[self].label

    @property
    def phrase(self) -> str:
        """The name as it reads inside a sentence, e.g. "Book annual leave?".

        Not ``label.lower()``: that is how "Book TOIL?" became "Book toil?" in
        six places at once. An acronym is lower case in no sentence.
        """
        return _DETAILS[self].phrase

    @property
    def short(self) -> str:
        """A one-word name, for a gauge label in a narrow sidebar.

        "Sickness" truncated to fit is "Sicknes", which reads as a typo rather
        than as an abbreviation.
        """
        return _DETAILS[self].short

    @property
    def token(self) -> str:
        """The stem of this type's CSS colour tokens, e.g. ``annual``.

        `flexi` is stored and `toil` is displayed: the database value is
        historical, and the colour token reads better beside the other four.
        """
        return _DETAILS[self].token

    @property
    def draws_down_entitlement(self) -> bool:
        """True when booking one costs a day of the annual allowance."""
        return self is AbsenceType.ANNUAL

    @property
    def draws_down_balance(self) -> bool:
        """True when booking one is a withdrawal from the flexi balance."""
        return self is AbsenceType.FLEXI

    @property
    def requires_note(self) -> bool:
        """True when a booking is meaningless without a written reason."""
        return self is AbsenceType.OTHER


CANCEL_WORD = "cancel"


def absence_from_word(word: str) -> AbsenceType | None:
    """The type a spoken word names, or ``None``.

    ``toil`` is the spoken name for the stored ``flexi`` value. The enum spells
    it ``flexi`` because that is what the balance is called, but
    ``flexi leave flexi tomorrow`` reads as a typo of the program name.
    """
    return _SPOKEN.get(word.strip().lower())


@dataclass(frozen=True, slots=True)
class _Details:
    """Everything an absence type carries besides its stored value."""

    label: str
    phrase: str
    short: str
    token: str


_DETAILS: dict[AbsenceType, _Details] = {
    AbsenceType.ANNUAL: _Details("Annual leave", "annual leave", "ANNUAL", "annual"),
    AbsenceType.SICK: _Details("Sickness", "sickness", "SICK", "sick"),
    AbsenceType.FLEXI: _Details("TOIL", "TOIL", "TOIL", "toil"),
    AbsenceType.UNPAID: _Details("Unpaid leave", "unpaid leave", "UNPAID", "unpaid"),
    AbsenceType.OTHER: _Details("Other", "other leave", "OTHER", "other"),
}
"""One table rather than three parallel ones.

The label, the short name and the colour token were three dicts keyed by member,
with a fourth derived from one of them. Adding a type and forgetting one was a
KeyError on the booking path with mypy clean and the suite green: four places to
remember and nothing to remind you.

Carrying the data on the members themselves, through `__new__`, would be
stronger still -- but it makes `AbsenceType("annual")` look like a four-argument
constructor to a type checker, and reading a stored value back out of the
database is the single commonest thing this enum does. One table and the check
below buys the same guarantee without spending that.
"""

_undeclared = set(AbsenceType) - set(_DETAILS)
if _undeclared:  # pragma: no cover - fails at import, before anything runs
    _names = ", ".join(sorted(kind.name for kind in _undeclared))
    _msg = f"AbsenceType members with no details declared: {_names}"
    raise RuntimeError(_msg)


class Verdict(enum.Enum):
    """What planning a booking decided about one date.

    Typed, because the old code told a bank holiday apart from a real refusal by
    looking for the words "bank holiday" in a sentence written for a status bar.
    That matched "That day is already a bank holiday" and missed "Bank holiday
    data unavailable; cannot book absence" on the capital B alone.
    """

    BOOK = "book"
    NON_WORKING = "non-working"
    BANK_HOLIDAY = "bank-holiday"
    NO_CALENDAR = "no-calendar"
    CLASH = "clash"
    NO_ENTITLEMENT = "no-entitlement"
    NEEDS_NOTE = "needs-note"

    @property
    def is_refusal(self) -> bool:
        """True when the day was asked for and could not be had.

        A weekend is not a refusal. Nobody booking a fortnight meant the
        Saturdays, and counting them as failures makes every fortnight partial.
        """
        return self not in {Verdict.BOOK, Verdict.NON_WORKING, Verdict.BANK_HOLIDAY}

    @property
    def is_skip(self) -> bool:
        """True when the date was passed over rather than refused."""
        return self in {Verdict.NON_WORKING, Verdict.BANK_HOLIDAY}


_SPOKEN: dict[str, AbsenceType] = {
    **{kind.token: kind for kind in AbsenceType},
    "flexi": AbsenceType.FLEXI,
    "holiday": AbsenceType.ANNUAL,
    "al": AbsenceType.ANNUAL,
    "leave": AbsenceType.ANNUAL,
}


class Portion(enum.Enum):
    """How much of a day an absence covers."""

    FULL = "full"
    AM = "am"
    PM = "pm"

    @property
    def days(self) -> float:
        """The fraction of a working day this portion consumes."""
        return 1.0 if self is Portion.FULL else 0.5

    @property
    def label(self) -> str:
        """The name shown to a reader."""
        return _PORTION_LABELS[self].label

    @property
    def noun(self) -> str:
        """What one of these is called when it is being counted.

        "2 mornings of TOIL" rather than "2 Mornings", and "5 days" rather than
        "5 full days", which is only worth saying beside a half.
        """
        return _PORTION_LABELS[self].noun


@dataclass(frozen=True, slots=True)
class _PortionNames:
    label: str
    noun: str


_PORTION_LABELS: dict[Portion, _PortionNames] = {
    Portion.FULL: _PortionNames("Full day", "day"),
    Portion.AM: _PortionNames("Morning", "morning"),
    Portion.PM: _PortionNames("Afternoon", "afternoon"),
}


class DayKind(StrEnum):
    """What a date is, at a glance.

    ``PARTIAL`` is the case a one-status-per-day table gets wrong: a half-day
    absence with work in the other half. It is why the records table has
    expandable rows.
    """

    WORKING = "working"
    WEEKEND = "weekend"
    HOLIDAY = "holiday"
    ABSENT = "absent"
    PARTIAL = "partial"
