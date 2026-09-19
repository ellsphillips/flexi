"""Enumerations shared across every layer.

Nothing here imports anything else in Flexi, so it can be imported from the
domain, the services and the widgets without a cycle.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = (
    "CANCEL_WORD",
    "DEFAULT_DIVISION",
    "AbsenceType",
    "ClockAction",
    "DayKind",
    "Division",
    "EventSource",
    "Granularity",
    "Portion",
    "Verdict",
    "absence_from_word",
)


class EventSource(StrEnum):
    """Who punched the clock.

    Migration 0010 tells these values apart to decide whose timestamps it may
    rewrite, so a wrong one is a silent data conversion, not an error.
    """

    USER = "user"
    """The user pressed a key."""

    SYSTEM = "system"
    """Flexi closed a session the user left open."""

    AMENDED = "amended"
    """The user recorded work after the fact. It counts for everything a
    punched session counts for, and is drawn apart from one."""


class Granularity(StrEnum):
    """The span a period covers."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"

    @property
    def label(self) -> str:
        """The name shown to a reader."""
        return self.value.capitalize()

    def next(self) -> Granularity:
        """The next granularity in the cycle ``day → week → month → year → day``."""
        order: list[Granularity] = list(Granularity)
        return order[(order.index(self) + 1) % len(order)]


class Division(StrEnum):
    """A GOV.UK bank holiday division, as the GOV.UK index keys it."""

    ENGLAND_AND_WALES = "england-and-wales"
    SCOTLAND = "scotland"
    NORTHERN_IRELAND = "northern-ireland"

    @property
    def label(self) -> str:
        """The name shown to a reader."""
        return _DIVISION_LABELS[self]

    @classmethod
    def choices(cls) -> tuple[tuple[str, str], ...]:
        """Label and value, for a Select or a click.Choice."""
        return tuple((member.label, member.value) for member in cls)


_DIVISION_LABELS: Final[Mapping[Division, str]] = MappingProxyType(
    {
        Division.ENGLAND_AND_WALES: "England & Wales",
        Division.SCOTLAND: "Scotland",
        Division.NORTHERN_IRELAND: "Northern Ireland",
    }
)

DEFAULT_DIVISION = Division.ENGLAND_AND_WALES
"""The division assumed until the user chooses one."""


class ClockAction(enum.Enum):
    """Clock action type persisted to the database."""

    IN = "in"
    OUT = "out"


class AbsenceType(enum.Enum):
    """A reason a working day was not worked.

    A bank holiday is not one of these: it is a property of the date, it comes
    from GOV.UK, and it cannot be created or removed from the interface.
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

        Not ``label.lower()``: an acronym such as TOIL stays upper case in a
        sentence.
        """
        return _DETAILS[self].phrase

    @property
    def short(self) -> str:
        """A one-word name, for a gauge label in a narrow sidebar."""
        return _DETAILS[self].short

    @property
    def token(self) -> str:
        """The stem of this type's CSS colour tokens, e.g. ``annual``.

        ``flexi`` is the value stored in the database and ``toil`` is its
        token.
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
    """Return the type a spoken word names, or ``None``.

    ``toil`` is the spoken name for the stored ``flexi`` value.
    """
    return _SPOKEN.get(word.strip().lower())


@dataclass(frozen=True, slots=True)
class _Details:
    """Everything an absence type carries besides its stored value."""

    label: str
    phrase: str
    short: str
    token: str


_DETAILS: Final[Mapping[AbsenceType, _Details]] = MappingProxyType(
    {
        AbsenceType.ANNUAL: _Details(
            "Annual leave", "annual leave", "ANNUAL", "annual"
        ),
        AbsenceType.SICK: _Details("Sickness", "sickness", "SICK", "sick"),
        AbsenceType.FLEXI: _Details("TOIL", "TOIL", "TOIL", "toil"),
        AbsenceType.UNPAID: _Details(
            "Unpaid leave", "unpaid leave", "UNPAID", "unpaid"
        ),
        AbsenceType.OTHER: _Details("Other", "other leave", "OTHER", "other"),
    }
)
"""One table keyed by member, behind the `AbsenceType` properties.

Carrying the same data on the members through `__new__` makes
`AbsenceType("annual")`, the way a stored value is read back, look like a
four-argument constructor to a type checker.
"""


class Verdict(enum.Enum):
    """The outcome of planning a booking for one date.

    Typed, so no caller has to tell a skip from a refusal by reading the words
    in a message written for a status bar.
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

        A weekend or a bank holiday is a skip, not a refusal: counting them as
        failures would make every fortnight partial.
        """
        return self not in {Verdict.BOOK, Verdict.NON_WORKING, Verdict.BANK_HOLIDAY}

    @property
    def is_skip(self) -> bool:
        """True when the date was passed over, not refused."""
        return self in {Verdict.NON_WORKING, Verdict.BANK_HOLIDAY}


_SPOKEN: Final[Mapping[str, AbsenceType]] = MappingProxyType(
    {
        **{kind.token: kind for kind in AbsenceType},
        "flexi": AbsenceType.FLEXI,
        "holiday": AbsenceType.ANNUAL,
        "al": AbsenceType.ANNUAL,
        "leave": AbsenceType.ANNUAL,
    }
)


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
        """The lower-case name this portion takes when counted, e.g. "morning"."""
        return _PORTION_LABELS[self].noun


@dataclass(frozen=True, slots=True)
class _PortionNames:
    label: str
    noun: str


_PORTION_LABELS: Final[Mapping[Portion, _PortionNames]] = MappingProxyType(
    {
        Portion.FULL: _PortionNames("Full day", "day"),
        Portion.AM: _PortionNames("Morning", "morning"),
        Portion.PM: _PortionNames("Afternoon", "afternoon"),
    }
)


class DayKind(StrEnum):
    """What a date is, at a glance.

    ``PARTIAL`` is a half-day absence with work in the other half, which one
    status per day cannot express: the records table expands such a row.
    """

    WORKING = "working"
    WEEKEND = "weekend"
    HOLIDAY = "holiday"
    ABSENT = "absent"
    PARTIAL = "partial"
    UNTRACKED = "untracked"
    """A date before Flexi was set up, so nothing is expected of it.

    A leave year usually starts months before Flexi is installed; counted as
    ordinary days, each untracked working day reads as a full day of deficit.
    """
