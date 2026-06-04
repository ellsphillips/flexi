"""Pure domain logic: dates, durations, periods, ledgers and the balance.

Nothing in this package may import ``textual`` or ``sqlalchemy``. That rule is
what makes the arithmetic testable without a terminal or a database, and it is
enforced by ``tests/test_layering.py``.
"""

from flexi.domain.balance import BalanceSummary, accumulate, expected_for, worked_from
from flexi.domain.format import clock, days, delta, hm, signed_days
from flexi.domain.ledger import AbsenceSlice, DayLedger, Segment
from flexi.domain.period import Granularity, Period
from flexi.domain.punch import Cell, Window, bucket_minutes, strip

__all__ = [
    "AbsenceSlice",
    "BalanceSummary",
    "Cell",
    "DayLedger",
    "Granularity",
    "Period",
    "Segment",
    "Window",
    "accumulate",
    "bucket_minutes",
    "clock",
    "days",
    "delta",
    "expected_for",
    "hm",
    "signed_days",
    "strip",
    "worked_from",
]
