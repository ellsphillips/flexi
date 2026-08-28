"""Composable persistence operations for work sessions.

Clock events are immutable, so opening or closing a session is a two-row write:
create an event, then link the one session that won the corresponding database
claim. The conditional writes stop two application sessions both reporting
success and leave no speculative event behind for the losing writer.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from flexi.constants import ClockAction, EventSource
from flexi.models.database.db import WorkSession
from flexi.models.database.moment import punched

__all__ = ("stage_clock_in", "stage_clock_out", "stage_correction")


def stage_clock_in(
    session: Session,
    opened_at: datetime,
    work_date: date,
    *,
    source: EventSource,
) -> WorkSession | None:
    """Stage a clock-in unless another writer already opened a session.

    SQLite's partial unique index is the final authority. ``ON CONFLICT`` turns
    the expected losing writer into ``None`` rather than leaking an
    ``IntegrityError`` through the result-oriented service API. Its speculative
    IN event is deleted in the same transaction, preserving the immutable audit
    trail without an orphan.
    """
    event = punched(ClockAction.IN, opened_at, source=source)
    session.add(event)
    session.flush()

    created_id = session.execute(
        insert(WorkSession)
        .values(clock_in_id=event.id, work_date=work_date)
        .on_conflict_do_nothing()
        .returning(WorkSession.id)
    ).scalar_one_or_none()
    if created_id is None:
        session.delete(event)
        return None
    return session.scalars(
        select(WorkSession).where(WorkSession.id == created_id)
    ).one()


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

    The caller owns the surrounding transaction.  ``False`` means another
    writer closed the session first; the losing candidate event is then staged
    for deletion so committing the transaction cannot leave an orphaned audit
    row.  A conditional SQL update, rather than an ORM assignment, makes the
    check and link one database operation even on SQLite, which has no row lock
    suitable for the preceding read.
    """
    event = punched(ClockAction.OUT, closed_at, source=source)
    session.add(event)
    session.flush()

    claimed_id = session.execute(
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
        .returning(WorkSession.id)
        .execution_options(synchronize_session="fetch")
    ).scalar_one_or_none()
    if claimed_id is None:
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

    Not `stage_clock_in` followed by `stage_clock_out`: those two are the live
    path and are conditional on there being no open session, which a correction
    for last Tuesday has nothing to do with. The partial unique index admits any
    number of *closed* sessions on a date, which is what makes a morning and an
    afternoon two corrections rather than one.

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
