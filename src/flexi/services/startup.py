"""Closing sessions left open on an earlier work date.

One half of the sweep `ClockService.sweep` runs; the other half, voiding
sessions too short to have been real, belongs to the clock service. Keeping
them apart keeps the import between them one-way.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import EventSource, Portion
from flexi.domain.balance import worked_from
from flexi.domain.format import short_date
from flexi.domain.ledger import MIDDAY_HOUR
from flexi.models.database.db import AbsenceDay, WorkSession
from flexi.models.database.moment import moment_of
from flexi.services.absence import covers_the_whole_day
from flexi.services.ledger import segment_of
from flexi.services.transactions import write_transaction
from flexi.services.work_sessions import stage_clock_out

__all__ = ("close_stale_sessions",)


def close_stale_sessions(
    session: Session,
    auto_close_time: time,
    *,
    contracted: timedelta,
    today: date | None = None,
) -> list[WorkSession]:
    """Auto-close open sessions from previous work dates.

    If auto_close_time precedes clock-in, use the day's final minute without
    moving before clock-in. Booked leave caps the inferred interval and adds
    a note, a half day once half of ``contracted`` is worked. The resulting
    ClockEvents are system-sourced.
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
            closed_at, explanation = _stopped_by_leave(
                session, ws, closed_at, contracted
            )
            if stage_clock_out(
                session,
                ws.id,
                closed_at,
                source=EventSource.SYSTEM,
                auto_closed=True,
            ):
                if explanation is not None:
                    ws.note = " · ".join(filter(None, (ws.note, explanation)))
                closed.append(ws)

    return closed


def _stopped_by_leave(
    session: Session, ws: WorkSession, closing: datetime, contracted: timedelta
) -> tuple[datetime, str | None]:
    """Where leave booked on its own day stops an inferred clock-out, and why.

    A day off in full stops it at the clock-in. A half day stops it when the
    clock card said to go home, once the day's work makes up the other half of
    ``contracted``, and never before noon: the auto-close time would count the
    booked half as worked as well.
    """
    opened = moment_of(ws.clock_in_event)
    stmt = select(AbsenceDay.portion).where(AbsenceDay.date == ws.work_date)
    booked = list(session.scalars(stmt))
    if covers_the_whole_day(booked):
        portion, stop = Portion.FULL, opened
    elif booked:
        portion = booked[0]
        earlier = session.scalars(
            select(WorkSession).where(
                WorkSession.work_date == ws.work_date,
                WorkSession.id != ws.id,
                WorkSession.voided.is_(False),
            )
        )
        owed = contracted / 2 - worked_from(map(segment_of, earlier), now=opened)
        noon = wallclock.local(datetime.combine(ws.work_date, time(MIDDAY_HOUR)))
        stop = max(noon, wallclock.advance(opened, max(owed, timedelta())))
    else:
        return closing, None
    if stop >= closing:
        return closing, None
    return stop, f"Auto-closed at booked {portion.noun} on {short_date(ws.work_date)}"
