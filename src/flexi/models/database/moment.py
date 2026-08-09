"""How a clock reading maps onto two columns, in one place.

The conversion had been written three different ways in three modules, two of
which stripped the zone where the third converted it. It is a function now, and
nothing outside this module touches either column directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flexi import wallclock
from flexi.models.database.db import ClockEvent


def columns(moment: datetime) -> tuple[datetime, int]:
    """A moment as the pair of values that go on disk."""
    pinned = wallclock.local(moment)
    offset = pinned.utcoffset() or timedelta()
    return pinned.replace(tzinfo=None), round(offset.total_seconds() / 60)


def moment_of(event: ClockEvent) -> datetime:
    """A stored event as the moment it recorded.

    A row with no offset was written before Flexi recorded one. It is read in
    the machine's own zone, which is the best available reading of it and is
    exactly what the application did with such a row before the column existed.
    """
    if event.utc_offset_minutes is None:
        return wallclock.local(event.timestamp)
    return event.timestamp.replace(
        tzinfo=timezone(timedelta(minutes=event.utc_offset_minutes))
    )
