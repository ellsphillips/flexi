"""Drawing a line under an untracked stretch.

An adjustment is one signed row with a date and a reason, counted like any other
term in the sum, and removable. Deleting the records instead would lose the
proof of what did happen and would not survive the next recomputation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.domain.format import delta, stamp
from flexi.models.database.db import BalanceAdjustment
from flexi.services.transactions import atomic, write_transaction

__all__ = ("OPENING_BALANCE", "AdjustmentResult", "AdjustmentService")

OPENING_BALANCE = "opening balance"
"""The reason a zeroing adjustment is recorded under."""


@dataclass(frozen=True)
class AdjustmentResult:
    """The outcome of an adjustment, and what to tell the user about it."""

    success: bool
    message: str
    adjustment: BalanceAdjustment | None = None
    warning: str | None = None


class AdjustmentService:
    """Read and write stored corrections to the flexi balance."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- reading ----------------------------------------------------------

    def all(self) -> list[BalanceAdjustment]:
        """Every correction ever recorded, newest first."""
        stmt = select(BalanceAdjustment).order_by(
            BalanceAdjustment.date.desc(), BalanceAdjustment.id.desc()
        )
        return list(self._session.execute(stmt).scalars())

    def first_after(self, when: date, until: date) -> BalanceAdjustment | None:
        """The earliest correction dated after ``when`` and no later than ``until``.

        A settlement is sized from the balance up to its own date, so a line
        drawn earlier than one already standing cannot see it and counts the
        period the two share twice. `zero_balance` asks this before sizing one.
        """
        stmt = (
            select(BalanceAdjustment)
            .where(BalanceAdjustment.date > when, BalanceAdjustment.date <= until)
            .order_by(BalanceAdjustment.date, BalanceAdjustment.id)
            .limit(1)
        )
        return self._session.execute(stmt).scalars().first()

    # --- writing ----------------------------------------------------------

    def record(self, when: date, amount: timedelta, reason: str) -> AdjustmentResult:
        """Validate, store and commit one correction."""
        with atomic(self._session):
            return self.stage_record(when, amount, reason)

    def stage_record(
        self, when: date, amount: timedelta, reason: str
    ) -> AdjustmentResult:
        """Validate and stage one correction in a caller-owned transaction.

        Rounded to whole minutes, the resolution every figure in the interface
        is shown at, and refused when it rounds to nothing.

        Staged and not committed, so a cross-service decision can read and stage
        its consequence under one writer reservation, with no stale-read window.
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
            created_at=wallclock.utc_now().replace(tzinfo=None),
        )
        self._session.add(row)
        return AdjustmentResult(
            True,
            f"Balance adjusted by {delta(timedelta(minutes=minutes))}"
            f" on {stamp(when, '%-d %b %Y')}",
            row,
        )

    def remove(self, adjustment_id: int) -> AdjustmentResult:
        """Undo a correction: it is one row, so it can go."""
        with write_transaction(self._session):
            row = self._session.get(BalanceAdjustment, adjustment_id)
            if row is None:
                return AdjustmentResult(False, "No such adjustment")
            self._session.delete(row)
        return AdjustmentResult(True, "Adjustment removed")
