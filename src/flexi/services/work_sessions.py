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
