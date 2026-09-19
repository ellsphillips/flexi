"""What is left in each allowance, and whether that is comfortable.

Values, not services: an allowance knows what it holds and what that means, and
nothing here reaches a database, so a dashboard widget can import one without
pulling SQLAlchemy in behind it.

Every allowance carries a pace, where the figure would be if it were spent
evenly, because "18.5 days" is comfortable or alarming depending on how much of
the leave year is left. Comfort is decided here, not in the widget that draws
it, so there is one rule and it is testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from flexi.constants import AbsenceType
from flexi.domain.balance import BalanceSummary

__all__ = ("PACE_TOLERANCE", "Allowance", "Pace", "WalletData")

PACE_TOLERANCE = 0.15
"""How far ahead of an even spread an allowance may run before it is flagged.

Fifteen per cent of a year's entitlement is about a long weekend on twenty-five
days, which is the noise of where school holidays fall.
"""


class Pace(Enum):
    """How an allowance is running against an even spread of the year."""

    UNKNOWN = "unknown"
    """No entitlement to run out of, or too early in the year to say."""

    ON_TRACK = "on track"
    AHEAD = "ahead"
    """Spent enough beyond the even spread to be worth mentioning."""


@dataclass(frozen=True, slots=True)
class Allowance:
    """A line of the wallet."""

    type: AbsenceType
    used: float
    """Days spent, counting a half as a half."""
    occurrences: int
    """How many separate bookings: two half-days are two occasions, one day."""
    total: float | None = None
    """The entitlement, where there is one. ``None`` means uncapped."""
    pace: float | None = None
    """Where ``used`` would be if the allowance were spent evenly over the year."""
    balance_days: float | None = None
    """For TOIL: days of flexi balance available, in place of an entitlement."""

    @property
    def label(self) -> str:
        """The name shown to a reader."""
        return self.type.label

    @property
    def token(self) -> str:
        """The stem of this allowance's colour tokens."""
        return self.type.token

    @property
    def remaining(self) -> float | None:
        """Days left, or ``None`` when nothing has been recorded to draw against.

        ``None`` is not zero, and the interface must not draw it as zero: a fresh
        install with no entitlement recorded has not run out of leave.
        """
        if self.type.draws_down_balance:
            return self.balance_days
        if self.total is None:
            return None
        return self.total - self.used

    @property
    def is_capped(self) -> bool:
        """True when there is an entitlement to run out of."""
        return self.total is not None

    @property
    def pace_state(self) -> Pace:
        """Whether this allowance is running ahead of an even spread.

        Measured against the entitlement, not against the pace, so the tolerance
        means the same on twenty-five days as on five.
        """
        if self.pace is None or self.total is None or not self.total:
            return Pace.UNKNOWN
        overspend = (self.used - self.pace) / self.total
        return Pace.AHEAD if overspend > PACE_TOLERANCE else Pace.ON_TRACK


@dataclass(frozen=True, slots=True)
class WalletData:
    """Everything the wallet module draws."""

    leave_year: tuple[date, date]
    balance: BalanceSummary
    """The running flexi balance, leave-year to date."""
    period: BalanceSummary
    """The same figures for the period currently on screen."""
    contracted: timedelta
    allowances: tuple[Allowance, ...]

    @property
    def balance_days(self) -> float:
        """The flexi balance expressed in working days."""
        if not self.contracted:
            return 0.0
        return self.balance.delta / self.contracted

    def allowance(self, kind: AbsenceType) -> Allowance:
        """The allowance of one type."""
        return next(item for item in self.allowances if item.type is kind)
