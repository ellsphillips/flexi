"""The ledger's loose ends.

Everything the ledger service computes is asserted through the surfaces that
draw it — `tests/tui/test_records.py`, `tests/components/test_charts.py` and the
balance tests in `tests/domain`. What is left here is the one module-level
helper none of those reach.
"""

from __future__ import annotations

from datetime import UTC, datetime

from flexi.services import ledger


def test_the_shared_now_is_an_aware_utc_moment() -> None:
    """Naive timestamps are the one thing the storage layer cannot recover from.

    :mod:`flexi.models.database.moment` splits an aware moment into a wall-clock
    column and an offset, and reassembles it on the way out. Hand it a naive
    datetime and the offset is a guess, so a record written in British Summer
    Time reads back an hour out and the day's total is wrong by an hour with
    nothing on screen to say so.

    ``utc_now`` currently has no callers; if it is deleted, delete this too.
    """
    moment = ledger.utc_now()

    assert moment.tzinfo is UTC
    assert abs(moment - datetime.now(tz=UTC)).total_seconds() < 60
