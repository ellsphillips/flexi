"""The arithmetic of a flexi balance.

    balance = Σ worked − Σ expected − Σ TOIL taken

``expected`` is the part that carries the meaning. A day you booked as annual
leave expects nothing, so it neither earns nor costs flexi. A day you worked six
hours against a 7h24 contract costs you 1h24. A Saturday you worked expects
nothing and so earns you the lot.

TOIL is subtracted separately rather than folded into ``expected``, because a
TOIL day is a *withdrawal from the same account the surplus accrues into*, not a
day you were expected to work. Folding it in would make a TOIL day look like an
ordinary absence and the balance would never go down.

Everything is :class:`~datetime.timedelta`. Hours only exist at the formatting
boundary — ``7.4`` is not representable in binary floating point, and a week of
rounding it produces a balance that disagrees with the sum of its own rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from flexi.constants import AbsenceType
from flexi.domain.ledger import AbsenceSlice, Segment

ZERO = timedelta()


def worked_from(segments: Iterable[Segment], now: datetime) -> timedelta:
    """How long was spent on the clock, counting any open session up to ``now``.

    An open session counts, which is what makes the balance tick up while it is
    being watched.
    """
    return sum((segment.duration(now) for segment in segments), start=ZERO)


def expected_for(
    contracted: timedelta,
    *,
    is_working_day: bool,
    is_holiday: bool,
    absences: Iterable[AbsenceSlice] = (),
) -> timedelta:
    """How much work a date asked for.

    Zero on a non-working day, a bank holiday, or a full day of absence of any
    type. Half the contract when exactly one half-day is booked; zero when both
    halves are, even if they are different types.
    """
    if not is_working_day or is_holiday:
        return ZERO
    booked = sum(slice_.portion.days for slice_ in absences)
    remaining = max(0.0, 1.0 - booked)
    return contracted * remaining


def toil_taken_for(
    contracted: timedelta,
    absences: Iterable[AbsenceSlice],
) -> timedelta:
    """How much of the flexi balance a date's TOIL bookings withdrew."""
    return sum(
        (
            contracted * slice_.portion.days
            for slice_ in absences
            if slice_.type is AbsenceType.FLEXI
        ),
        start=ZERO,
    )


@dataclass(frozen=True, slots=True)
class BalanceSummary:
    """Worked, expected and withdrawn, accumulated over a span of dates."""

    worked: timedelta = ZERO
    expected: timedelta = ZERO
    toil_taken: timedelta = ZERO

    @property
    def delta(self) -> timedelta:
        """The signed balance: what this span did to the flexi account."""
        return self.worked - self.expected - self.toil_taken

    @property
    def is_surplus(self) -> bool:
        """True when the span finished ahead of its contracted hours."""
        return self.delta > ZERO

    @property
    def is_deficit(self) -> bool:
        """True when the span finished behind its contracted hours."""
        return self.delta < ZERO

    def __add__(self, other: BalanceSummary) -> BalanceSummary:
        return BalanceSummary(
            self.worked + other.worked,
            self.expected + other.expected,
            self.toil_taken + other.toil_taken,
        )


def accumulate(ledgers: Iterable[object]) -> BalanceSummary:
    """Total a run of day ledgers.

    Typed loosely on purpose: this is called with
    :class:`~flexi.domain.ledger.DayLedger` values, and typing it as such would
    make ``ledger`` and ``balance`` import each other. The three attributes it
    reads are the contract.
    """
    total = BalanceSummary()
    for ledger in ledgers:
        total = total + BalanceSummary(
            worked=getattr(ledger, "worked", ZERO),
            expected=getattr(ledger, "expected", ZERO),
            toil_taken=getattr(ledger, "toil_taken", ZERO),
        )
    return total
