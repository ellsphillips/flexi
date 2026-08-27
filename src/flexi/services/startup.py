"""Closing the sessions nobody closed.

One half of the sweep `ClockService.sweep` runs; the other half, voiding
sessions too short to have been real, is the clock's own. This module used to
hold the pair, which meant importing `ClockService` for a type annotation --
and `ClockService` importing this back, inside a method, purely to make the
cycle importable.
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import EventSource
from flexi.models.database.db import WorkSession
from flexi.models.database.moment import moment_of
from flexi.services.transactions import atomic
from flexi.services.work_sessions import stage_clock_out

__all__ = ("close_stale_sessions",)


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
        WorkSession.voided.is_(False),
        WorkSession.work_date < today,
    )
    stale = list(session.execute(stmt).scalars())

    if not stale:
        return []

    closed: list[WorkSession] = []
    with atomic(session):
        for ws in stale:
            opened = moment_of(ws.clock_in_event)

            # If configured close is before clock-in, use 23:59
            effective_close = auto_close_time
            if effective_close < opened.time():
                effective_close = time(23, 59)

            closed_at = wallclock.local(datetime.combine(ws.work_date, effective_close))
            if stage_clock_out(
                session,
                ws.id,
                closed_at,
                source=EventSource.SYSTEM,
                auto_closed=True,
            ):
                closed.append(ws)

    return closed
