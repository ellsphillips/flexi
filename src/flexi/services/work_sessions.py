"""Composable persistence operations for work sessions.

Clock events are immutable, so closing a session is a two-row write: create an
OUT event, then link the still-open session to it.  The link is conditional so
two application sessions cannot both report success while leaving one event
orphaned.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from flexi.constants import ClockAction, EventSource
from flexi.models.database.db import WorkSession
from flexi.models.database.moment import punched

__all__ = ("stage_clock_out",)


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
