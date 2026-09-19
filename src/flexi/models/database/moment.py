"""How a clock reading maps onto two columns, in one place.

Nothing outside this module touches ``timestamp`` or ``utc_offset_minutes``
directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flexi import wallclock
from flexi.constants import ClockAction, EventSource
from flexi.models.database.db import ClockEvent

__all__ = ("moment_of", "punched")


def punched(
    action: ClockAction,
    moment: datetime,
    *,
    source: EventSource = EventSource.USER,
) -> ClockEvent:
    """A clock event for a moment, with both of its columns filled from it."""
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

    ``utc_offset_minutes`` is ``None`` on a row that predates the column; such
    a row is read in the machine's own zone.
    """
    if event.utc_offset_minutes is None:
        return wallclock.local(event.timestamp)
    return event.timestamp.replace(
        tzinfo=timezone(timedelta(minutes=event.utc_offset_minutes))
    )
