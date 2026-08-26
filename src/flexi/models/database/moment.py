"""How a clock reading maps onto two columns, in one place.

The conversion had been written three different ways in three modules, two of
which stripped the zone where the third converted it. It is a function now, and
nothing outside this module touches either column directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flexi import wallclock
from flexi.constants import ClockAction, EventSource
from flexi.models.database.db import ClockEvent


def punched(
    action: ClockAction,
    moment: datetime,
    *,
    source: EventSource = EventSource.USER,
) -> ClockEvent:
    """A clock event for a moment, with both of its columns filled from it.

    The module promised that nothing outside it touches either column, and five
    call sites did -- one of which had its own copy of the offset arithmetic,
    computed from the same wall reading by a different route.
    """
    pinned = wallclock.local(moment)
    offset = pinned.utcoffset() or timedelta()
    return ClockEvent(
        action=action,
        timestamp=pinned.replace(tzinfo=None),
        utc_offset_minutes=round(offset.total_seconds() / 60),
        source=source,
    )


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
