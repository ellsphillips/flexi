"""Startup routines that run before any clock action or app launch."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi.constants import ClockAction
from flexi.models.database.db import ClockEvent, WorkSession
from flexi.services.settings import SettingsService


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
        today = date.today()

    stmt = select(WorkSession).where(
        WorkSession.clock_out_id.is_(None),
        WorkSession.work_date < today,
    )
    stale = list(session.execute(stmt).scalars())

    closed: list[WorkSession] = []
    for ws in stale:
        clock_in_time = ws.clock_in_event.timestamp.replace(tzinfo=None).time()

        # If configured close is before clock-in, use 23:59
        effective_close = auto_close_time
        if effective_close <= clock_in_time:
            effective_close = time(23, 59)

        close_dt = datetime.combine(ws.work_date, effective_close, tzinfo=UTC)
        event = ClockEvent(
            action=ClockAction.OUT,
            timestamp=close_dt,
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


def run_startup_cleanup(session: Session) -> list[WorkSession]:
    """Run all startup-time cleanup. Called before app launch and clock actions.

    Two sweeps. Sessions left running overnight are closed at the configured
    time, and sessions so short they can only have been a slip of the finger are
    voided — which also cleans up databases that predate the threshold.
    """
    from flexi.config import CONFIG
    from flexi.services.clock import ClockService

    settings = SettingsService(session)
    closed = close_stale_sessions(session, settings.get_auto_close_time())
    ClockService(
        session, timedelta(seconds=CONFIG.defaults.minimum_session_seconds)
    ).discard_short_sessions()
    return closed
