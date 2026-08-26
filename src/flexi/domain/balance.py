"""The arithmetic of a flexi balance.

    balance = Σ worked − Σ expected − Σ TOIL taken + Σ adjustments

TOIL is subtracted separately rather than folded into ``expected``: it is a
withdrawal from the account the surplus accrues into, not a day nobody expected
you to work, and folding it in would stop the balance ever going down.

Everything is a :class:`~datetime.timedelta`. Hours exist only at the formatting
boundary, because 7.4 is not representable in binary floating point and a leave
year of rounding it gives a balance that disagrees with the sum of its rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from flexi.constants import AbsenceType
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment

ZERO = timedelta()


def worked_from(segments: Iterable[Segment], now: datetime) -> timedelta:
    """Time on the clock, counting an open session up to ``now``.

    ``now`` must be a local moment carrying its offset -- see
    :mod:`flexi.wallclock`. A naive one raises rather than quietly returning the
    wall difference, which is the failure this signature is shaped to force.
    """
    return sum((segment.duration(now) for segment in segments), start=ZERO)


def expected_for(
    contracted: timedelta,
    *,
    is_tracked: bool,
    is_working_day: bool,
    is_holiday: bool,
    absences: Iterable[AbsenceSlice] = (),
) -> timedelta:
    """How much work a date asked for.

    Zero on a day before Flexi was tracking, a non-working day, a bank holiday,
    or a full day of absence of any type. Half the contract for one half-day;
    zero for two, even of different types.

    ``is_tracked`` is required rather than defaulting to true. The default would
    be the behaviour this argument exists to correct, which is the one value a
    caller must not be able to arrive at by forgetting.
    """
    if not is_tracked or not is_working_day or is_holiday:
        return ZERO
    booked = sum(slice_.portion.days for slice_ in absences)
    remaining = max(0.0, 1.0 - booked)
    return contracted * remaining


def toil_taken_for(
    contracted: timedelta,
    absences: Iterable[AbsenceSlice],
) -> timedelta:
    """How much of the balance a date's TOIL bookings withdrew."""
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
    adjustment: timedelta = ZERO
    """The only term stored rather than derived from clock events."""

    @property
    def delta(self) -> timedelta:
        """What this span did to the flexi account."""
        return self.worked - self.expected - self.toil_taken + self.adjustment

    @property
    def is_surplus(self) -> bool:
        return self.delta > ZERO

    @property
    def is_deficit(self) -> bool:
        return self.delta < ZERO

    def __add__(self, other: BalanceSummary) -> BalanceSummary:
        return BalanceSummary(
            self.worked + other.worked,
            self.expected + other.expected,
            self.toil_taken + other.toil_taken,
            self.adjustment + other.adjustment,
        )


def accumulate(ledgers: Iterable[DayLedger]) -> BalanceSummary:
    """Total a run of day ledgers."""
    total = BalanceSummary()
    for ledger in ledgers:
        total = total + BalanceSummary(
            worked=ledger.worked,
            expected=ledger.expected,
            toil_taken=ledger.toil_taken,
            adjustment=ledger.adjustment,
        )
    return total
