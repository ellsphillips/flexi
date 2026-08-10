"""Enumerations shared across every layer.

Nothing here imports anything else in Flexi, so it can be imported from the
domain, the services and the widgets without a cycle.
"""

from __future__ import annotations

import enum
from enum import StrEnum, auto


class StatusOption(StrEnum):
    """Actions for clocking in or out."""

    ARRIVE = auto()
    DEPART = auto()

    @classmethod
    def from_str(cls, action: str) -> StatusOption:
        """The option named by a word or by its initial."""
        return cls.ARRIVE if action.lower().startswith("a") else cls.DEPART


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
        return _ABSENCE_LABELS[self]

    @property
    def short(self) -> str:
        """A one-word name, for a gauge label in a narrow sidebar.

        "Sickness" truncated to fit is "Sicknes", which reads as a typo rather
        than as an abbreviation.
        """
        return _ABSENCE_SHORT[self]

    @property
    def token(self) -> str:
        """The stem of this type's CSS colour tokens, e.g. ``annual``."""
        return _ABSENCE_TOKENS[self]

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


_ABSENCE_LABELS: dict[AbsenceType, str] = {
    AbsenceType.ANNUAL: "Annual leave",
    AbsenceType.SICK: "Sickness",
    AbsenceType.FLEXI: "TOIL",
    AbsenceType.UNPAID: "Unpaid leave",
    AbsenceType.OTHER: "Other",
}

_ABSENCE_SHORT: dict[AbsenceType, str] = {
    AbsenceType.ANNUAL: "ANNUAL",
    AbsenceType.SICK: "SICK",
    AbsenceType.FLEXI: "TOIL",
    AbsenceType.UNPAID: "UNPAID",
    AbsenceType.OTHER: "OTHER",
}

# `flexi` is stored, `toil` is displayed and themed: the database value is
# historical, and the colour token reads better beside the other four.
CANCEL_WORD = "cancel"


def absence_from_word(word: str) -> AbsenceType | None:
    """The type a spoken word names, or ``None``.

    ``toil`` is the spoken name for the stored ``flexi`` value. The enum spells
    it ``flexi`` because that is what the balance is called, but
    ``flexi leave flexi tomorrow`` reads as a typo of the program name.
    """
    return _SPOKEN.get(word.strip().lower())


_ABSENCE_TOKENS: dict[AbsenceType, str] = {
    AbsenceType.ANNUAL: "annual",
    AbsenceType.SICK: "sick",
    AbsenceType.FLEXI: "toil",
    AbsenceType.UNPAID: "unpaid",
    AbsenceType.OTHER: "other",
}


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
    **{token: kind for kind, token in _ABSENCE_TOKENS.items()},
    "flexi": AbsenceType.FLEXI,
    "holiday": AbsenceType.ANNUAL,
    "al": AbsenceType.ANNUAL,
    "leave": AbsenceType.ANNUAL,
}

LEAVE_WORDS: frozenset[str] = frozenset({*_SPOKEN, CANCEL_WORD})


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
        return _PORTION_LABELS[self]


_PORTION_LABELS: dict[Portion, str] = {
    Portion.FULL: "Full day",
    Portion.AM: "Morning",
    Portion.PM: "Afternoon",
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
