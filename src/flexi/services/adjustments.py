"""Drawing a line under an untracked stretch, or bringing a balance in by hand.

An adjustment is one signed row with a date and a reason, counted like any other
term in the sum, and removable. Deleting the records instead would lose the
proof of what did happen and would not survive the next recomputation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.domain.format import MINUS, delta, stamp
from flexi.models.database.db import BalanceAdjustment
from flexi.services.transactions import atomic, write_transaction

__all__ = ("SETTLED", "AdjustmentResult", "AdjustmentService", "parse_amount")

SETTLED = "settled"
"""The reason a zeroing adjustment is recorded under when none is given.

Not "opening balance": that is what a balance brought in from elsewhere is, and
`flexi balance log` has to tell the two apart."""

_AMOUNT = re.compile(rf"([+\-{MINUS}]?)(\d{{1,4}}):([0-5]\d)")
"""Four digits of hours at most, which no leave year's balance reaches."""


def parse_amount(raw: str) -> timedelta:
    """A signed ``h:mm``, read the way Flexi writes one.

    No sign is a surplus. A deficit takes a hyphen, or the U+2212 minus Flexi
    draws its figures with, so a balance pasted from the screen reads back. A
    bare number is refused, since nothing says whether it is hours or minutes,
    and so is zero, which would change nothing.

    Examples:
        >>> parse_amount("+5:30") == timedelta(hours=5, minutes=30)
        True
        >>> parse_amount("−1:30") == -timedelta(hours=1, minutes=30)
        True
    """
    found = _AMOUNT.fullmatch(raw.strip())
    if found is None:
        msg = f"'{raw}' is not an amount: use H:MM, like +5:30 or -1:30"
        raise ValueError(msg)
    sign, hours, minutes = found.groups()
    amount = timedelta(hours=int(hours), minutes=int(minutes))
    if not amount:
        msg = "An adjustment of 0:00 would change nothing"
        raise ValueError(msg)
    return amount if sign in {"", "+"} else -amount


@dataclass(frozen=True)
class AdjustmentResult:
    """The outcome of an adjustment, and what to tell the user about it."""

    success: bool
    message: str
    adjustment: BalanceAdjustment | None = None
    warning: str | None = None


def _may_settle(row: BalanceAdjustment) -> bool:
    """Whether ``row`` may be a settlement, which its reason cannot say.

    `zero_balance` settles finished days only, so a settlement is always dated
    before the day it was made, and a row dated on or after that day, as an
    adjustment from today is, never is one. ``created_at`` is UTC; the day it
    was made is read on the local clock, as `zero_balance` reads today.
    """
    made = wallclock.local(row.created_at.replace(tzinfo=UTC)).date()
    return row.date < made


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

    def first_line_after(self, when: date, until: date) -> BalanceAdjustment | None:
        """The earliest row after ``when``, up to ``until``, that may be a settlement.

        A settlement is sized from the balance up to its own date, so a line
        drawn earlier than one already standing cannot see it and counts the
        period the two share twice. `zero_balance` asks this before sizing one.
        """
        stmt = (
            select(BalanceAdjustment)
            .where(BalanceAdjustment.date > when, BalanceAdjustment.date <= until)
            .order_by(BalanceAdjustment.date, BalanceAdjustment.id)
        )
        return next(filter(_may_settle, self._session.execute(stmt).scalars()), None)

    def last_line_before(self, when: date) -> BalanceAdjustment | None:
        """The latest row before ``when`` that may be a settlement.

        The mirror of :meth:`first_line_after`: `adjust_balance` asks it for
        the line a new correction has to be dated after.
        """
        stmt = (
            select(BalanceAdjustment)
            .where(BalanceAdjustment.date < when)
            .order_by(BalanceAdjustment.date.desc(), BalanceAdjustment.id.desc())
        )
        return next(filter(_may_settle, self._session.execute(stmt).scalars()), None)

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
