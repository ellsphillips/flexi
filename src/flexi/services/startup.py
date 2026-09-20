"""Closing sessions left open on an earlier work date.

One half of the sweep `ClockService.sweep` runs; the other half, voiding
sessions too short to have been real, belongs to the clock service. Keeping
them apart keeps the import between them one-way.
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import EventSource
from flexi.domain.format import short_date
from flexi.models.database.db import WorkSession
from flexi.models.database.moment import moment_of
from flexi.services.transactions import write_transaction
from flexi.services.work_sessions import first_absence_overlap, stage_clock_out

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
    with write_transaction(session):
        for ws in stale:
            opened = moment_of(ws.clock_in_event)

            # A close before its own clock-in is a negative segment, which the
            # ledger subtracts instead of clamping, so fall back to 23:59, or
            # to the clock-in itself on the half minute later than that.
            effective_close = auto_close_time
            if effective_close < opened.time():
                effective_close = max(time(23, 59), opened.time())

            wall_close = datetime.combine(ws.work_date, effective_close)
            closed_at = wallclock.local(wall_close)
            if closed_at < opened:
                # The first occurrence of a repeated hour may precede a clock-in
                # in its second occurrence. A changed machine timezone can make
                # both readings earlier, in which case retain a zero-length span.
                closed_at = max(opened, wallclock.local(wall_close.replace(fold=1)))
            clash = first_absence_overlap(session, opened, closed_at)
            if clash is not None:
                booking, closed_at = clash
            if stage_clock_out(
                session,
                ws.id,
                closed_at,
                source=EventSource.SYSTEM,
                auto_closed=True,
            ):
                if clash is not None:
                    explanation = (
                        f"Auto-closed at booked {booking.portion.noun} "
                        f"on {short_date(booking.date)}"
                    )
                    ws.note = " · ".join(filter(None, (ws.note, explanation)))
                closed.append(ws)

    return closed
