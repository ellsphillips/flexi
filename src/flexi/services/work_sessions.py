"""Composable persistence operations for work sessions.

Clock events are immutable, so opening or closing a session is a two-row write:
create an event, then link the one session that won the corresponding database
claim. The conditional writes stop two application sessions both reporting
success and leave no speculative event behind for the losing writer.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, selectinload

from flexi import wallclock
from flexi.constants import ClockAction, EventSource, Portion
from flexi.domain.ledger import MIDDAY_HOUR
from flexi.models.database.db import AbsenceDay, ClockEvent, WorkSession
from flexi.models.database.moment import punched

__all__ = (
    "first_absence_overlap",
    "sessions_touching",
    "stage_clock_in",
    "stage_clock_out",
    "stage_correction",
)


def first_absence_overlap(
    session: Session, start: datetime, end: datetime
) -> tuple[AbsenceDay, datetime] | None:
    """The first booked absence touched by work, and where the overlap begins.

    Endpoints may touch: work ending at noon leaves an afternoon booking intact.
    The caller reserves the writer before using this answer to persist work.
    """
    stmt = (
        select(AbsenceDay)
        .where(AbsenceDay.date >= start.date(), AbsenceDay.date <= end.date())
        .order_by(AbsenceDay.date, AbsenceDay.portion)
    )
    for absence in session.scalars(stmt):
        midday = datetime.combine(absence.date, time(MIDDAY_HOUR))
        midnight = datetime.combine(absence.date, time.min)
        begins = wallclock.local(midday if absence.portion is Portion.PM else midnight)
        finishes = wallclock.local(
            midday if absence.portion is Portion.AM else midnight + timedelta(days=1)
        )
        if start < finishes and begins < end:
            return absence, max(start, begins)
    return None


def sessions_touching(session: Session, start: date, end: date) -> list[WorkSession]:
    """Load active work that can overlap a date range, including overnight work.

    A session is filed under its opening date, but its recorded clock-out can
    claim time on later dates. Open sessions are included for the caller to
    resolve against its own cutoff. Both events are loaded in bounded queries.
    """
    stmt = (
        select(WorkSession)
        .outerjoin(ClockEvent, WorkSession.clock_out_id == ClockEvent.id)
        .options(
            selectinload(WorkSession.clock_in_event),
            selectinload(WorkSession.clock_out_event),
        )
        .where(
            WorkSession.work_date <= end,
            WorkSession.voided.is_(False),
            or_(
                WorkSession.work_date >= start,
                WorkSession.clock_out_id.is_(None),
                ClockEvent.timestamp > datetime.combine(start, time.min),
            ),
        )
        .order_by(WorkSession.work_date, WorkSession.id)
    )
    return list(session.scalars(stmt))


def stage_clock_in(
    session: Session,
    opened_at: datetime,
    work_date: date,
    *,
    source: EventSource,
) -> WorkSession | None:
    """Stage a clock-in unless another writer already opened a session.

    SQLite's partial unique index is the authority: ``ON CONFLICT`` turns the
    losing writer into ``None`` instead of an ``IntegrityError``, and its
    speculative IN event is deleted in the same transaction. The row is found
    by its unique clock-in, ``RETURNING`` arriving only in SQLite 3.35, which
    is newer than the libsqlite3 several supported distributions ship.
    """
    event = punched(ClockAction.IN, opened_at, source=source)
    session.add(event)
    session.flush()

    session.execute(
        insert(WorkSession)
        .values(clock_in_id=event.id, work_date=work_date)
        .on_conflict_do_nothing()
    )
    created = session.scalars(
        select(WorkSession).where(WorkSession.clock_in_id == event.id)
    ).one_or_none()
    if created is None:
        session.delete(event)
        return None
    return created


def stage_clock_out(
    session: Session,
    work_session_id: int,
    closed_at: datetime,
    *,
    source: EventSource,
    auto_closed: bool = False,
    voided: bool = False,
) -> bool:
    """Stage a clock-out only if ``work_session_id`` is still open.

    The caller owns the surrounding transaction. ``False`` means another writer
    closed the session first, and the losing event is staged for deletion so
    the commit leaves no orphaned audit row. The conditional SQL update makes
    the check and the link one database operation, SQLite having no row lock
    for the preceding read; the winner is read back off the unique clock-out
    column, ``RETURNING`` arriving only in SQLite 3.35.
    """
    event = punched(ClockAction.OUT, closed_at, source=source)
    session.add(event)
    session.flush()

    session.execute(
        update(WorkSession)
        .where(
            WorkSession.id == work_session_id,
            WorkSession.clock_out_id.is_(None),
            WorkSession.voided.is_(False),
        )
        .values(
            clock_out_id=event.id,
            auto_closed=auto_closed,
            voided=voided,
        )
        .execution_options(synchronize_session="evaluate")
    )
    claimed = session.scalars(
        select(WorkSession).where(WorkSession.clock_out_id == event.id)
    ).one_or_none()
    if claimed is None:
        session.delete(event)
        return False
    return True


def stage_correction(
    session: Session,
    opened_at: datetime,
    closed_at: datetime,
    work_date: date,
) -> WorkSession:
    """Stage a whole session that was never punched, open and closed at once.

    Not `stage_clock_in` then `stage_clock_out`: those are conditional on there
    being no open session. The partial unique index admits any number of closed
    sessions on one date, so a morning and an afternoon are two corrections.

    The caller owns the transaction and the validation. This writes.
    """
    started = punched(ClockAction.IN, opened_at, source=EventSource.AMENDED)
    ended = punched(ClockAction.OUT, closed_at, source=EventSource.AMENDED)
    session.add_all([started, ended])
    session.flush()

    recorded = WorkSession(
        clock_in_id=started.id,
        clock_out_id=ended.id,
        work_date=work_date,
    )
    session.add(recorded)
    session.flush()
    return recorded
