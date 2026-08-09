"""Drawing a line under a stretch nobody tracked.

Install Flexi in August against a leave year that began the previous October and
two hundred untracked working days each expect their contracted hours, so the
balance opens at minus ninety.

Deleting the records would lose the proof of what did happen and would not
survive the next recomputation. An adjustment is one signed row with a date and
a reason, counted like any other term in the sum, and removable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi.domain.format import stamp
from flexi.models.database.db import BalanceAdjustment

OPENING_BALANCE = "opening balance"
"""The reason a zeroing adjustment is recorded under."""


@dataclass(frozen=True)
class AdjustmentResult:
    """The outcome of an adjustment, and what to tell the user about it."""

    success: bool
    message: str
    adjustment: BalanceAdjustment | None = None


class AdjustmentService:
    """Read and write stored corrections to the flexi balance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # -- reading -----------------------------------------------------------

    def up_to(self, as_of: date) -> timedelta:
        """Every correction effective on or before ``as_of``, totalled."""
        stmt = select(BalanceAdjustment).where(BalanceAdjustment.date <= as_of)
        rows = self._session.execute(stmt).scalars()
        return timedelta(minutes=sum(row.minutes for row in rows))

    def in_range(self, start: date, end: date) -> list[BalanceAdjustment]:
        """Corrections effective within a span, in date order."""
        stmt = (
            select(BalanceAdjustment)
            .where(BalanceAdjustment.date >= start, BalanceAdjustment.date <= end)
            .order_by(BalanceAdjustment.date, BalanceAdjustment.id)
        )
        return list(self._session.execute(stmt).scalars())

    def all(self) -> list[BalanceAdjustment]:
        """Every correction ever recorded, newest first."""
        stmt = select(BalanceAdjustment).order_by(
            BalanceAdjustment.date.desc(), BalanceAdjustment.id.desc()
        )
        return list(self._session.execute(stmt).scalars())

    # -- writing -----------------------------------------------------------

    def record(self, when: date, amount: timedelta, reason: str) -> AdjustmentResult:
        """Store a correction.

        Rounded to whole minutes, because that is the resolution every figure in
        the interface is shown at and a correction that reads as ``+0:00`` while
        moving the balance by forty seconds is worse than no correction at all.
        """
        if not reason.strip():
            return AdjustmentResult(False, "An adjustment needs a reason")

        minutes = round(amount.total_seconds() / 60)
        if minutes == 0:
            return AdjustmentResult(False, "That adjustment would be zero minutes")

        row = BalanceAdjustment(
            date=when,
            minutes=minutes,
            reason=reason.strip(),
            created_at=datetime.now(tz=UTC).replace(tzinfo=None),
        )
        self._session.add(row)
        self._session.commit()
        return AdjustmentResult(
            True,
            f"Balance adjusted by {minutes:+d} minutes on {stamp(when, '%-d %b %Y')}",
            row,
        )

    def remove(self, adjustment_id: int) -> AdjustmentResult:
        """Undo a correction. It is one row, so it can simply go."""
        row = self._session.get(BalanceAdjustment, adjustment_id)
        if row is None:
            return AdjustmentResult(False, "No such adjustment")
        self._session.delete(row)
        self._session.commit()
        return AdjustmentResult(True, "Adjustment removed")
