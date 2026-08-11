"""Startup routines that run before any clock action or app launch."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from flexi.services.clock import ClockService

from flexi import wallclock
from flexi.constants import ClockAction
from flexi.models.database.db import ClockEvent, WorkSession
from flexi.models.database.moment import columns, moment_of


def close_stale_sessions(
    session: Session,
    auto_close_time: time,
    *,
    today: date | None = None,
) -> list[WorkSession]:
    """Auto-close open sessions from previous work dates.

    If auto_close_time is before the session's clock-in time,
    close at 23:59 instead. Creates system-sourced ClockEvents
    and marks sessions auto_closed.
    """
    if today is None:
        today = wallclock.today()

    stmt = select(WorkSession).where(
        WorkSession.clock_out_id.is_(None),
        WorkSession.work_date < today,
    )
    stale = list(session.execute(stmt).scalars())

    closed: list[WorkSession] = []
    for ws in stale:
        opened = moment_of(ws.clock_in_event)

        # If configured close is before clock-in, use 23:59
        effective_close = auto_close_time
        if effective_close <= opened.time():
            effective_close = time(23, 59)

        closed_at = wallclock.local(datetime.combine(ws.work_date, effective_close))
        wall, offset = columns(closed_at)
        event = ClockEvent(
            action=ClockAction.OUT,
            timestamp=wall,
            utc_offset_minutes=offset,
            source="system",
        )
        session.add(event)
        session.flush()
        ws.clock_out_id = event.id
        ws.auto_closed = True
        closed.append(ws)

    if closed:
        session.commit()

    return closed


def run_startup_cleanup(
    session: Session, clock: ClockService, auto_close: time
) -> list[WorkSession]:
    """Run all startup-time cleanup. Called before app launch and clock actions.

    Two sweeps. Sessions left running overnight are closed at the configured
    time, and sessions so short they can only have been a slip of the finger are
    voided — which also cleans up databases that predate the threshold.

    The clock service is passed in rather than built here. Building one meant a
    deferred import purely to make a cycle importable, and it meant this swept
    with the configured threshold while the caller that asked for the sweep held
    a different one. Now there is one edge, clock to startup, and one threshold.
    """
    closed = close_stale_sessions(session, auto_close)
    clock.discard_short_sessions()
    return closed
