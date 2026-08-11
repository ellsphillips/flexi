"""What is left in each allowance, and whether that is comfortable.

These are values, not services: an allowance knows what it holds and what that
means, and nothing here can reach a database. They lived in
``flexi.services.wallet``, which meant a dashboard widget importing one dragged
a hundred and twenty SQLAlchemy modules behind it -- and satisfied the layering
rule while defeating it, because the rule named ``sqlalchemy`` and the widget
imported ``flexi.services``.

Every allowance carries a pace -- where the figure would be if it were being
spent evenly -- because "18.5 days" is comfortable or alarming depending
entirely on how much of the leave year is left. Whether it is comfortable is
decided here rather than in the widget that colours it, so the rule is testable
and there is one of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from flexi.constants import AbsenceType
from flexi.domain.balance import BalanceSummary
from flexi.domain.ledger import DayLedger

PACE_TOLERANCE = 0.15
"""How far ahead of an even spread an allowance may run before it is flagged.

Fifteen per cent of a year's entitlement is roughly a long weekend on twenty-five
days -- close enough to the noise of when school holidays fall that flagging
anything tighter would cry wolf every April.
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
    """One line of the wallet."""

    type: AbsenceType
    used: float
    """Days spent, counting a half as a half."""
    occurrences: int
    """How many separate bookings — two half-days are two occasions, one day."""
    total: float | None = None
    """The entitlement, where there is one. ``None`` means uncapped."""
    pace: float | None = None
    """Where ``used`` would be if the allowance were spent evenly over the year."""
    balance_days: float | None = None
    """For TOIL: days of flexi balance available rather than an entitlement."""

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

        Measured against the entitlement rather than against the pace, so the
        tolerance means the same thing on twenty-five days as on five: a day and
        a half either way, not a proportion of however far through the year it
        happens to be.
        """
        if self.pace is None or self.total is None or not self.total:
            return Pace.UNKNOWN
        overspend = (self.used - self.pace) / self.total
        return Pace.AHEAD if overspend > PACE_TOLERANCE else Pace.ON_TRACK


@dataclass(frozen=True, slots=True)
class WalletData:
    """Everything the wallet module draws."""

    leave_year: tuple[date, date]
    elapsed: float
    """How far through the leave year today is, 0.0 to 1.0."""
    balance: BalanceSummary
    """The running flexi balance, leave-year to date."""
    period: BalanceSummary
    """The same figures for the period currently on screen."""
    today: DayLedger
    contracted: timedelta
    allowances: tuple[Allowance, ...]

    @property
    def balance_days(self) -> float:
        """The flexi balance expressed in working days."""
        if not self.contracted:
            return 0.0
        return self.balance.delta / self.contracted

    def allowance(self, kind: AbsenceType) -> Allowance:
        """One allowance by type."""
        return next(item for item in self.allowances if item.type is kind)
