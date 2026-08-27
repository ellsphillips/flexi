"""Reusable database DDL for invariants SQLAlchemy cannot model directly.

SQLite has no immutable-row constraint.  A trigger is therefore the one source
of truth for both schemas Flexi supports: :meth:`Base.metadata.create_all`
registers it as table DDL, while migration 0012 executes the same public SQL.
"""

from __future__ import annotations

from sqlalchemy import Table, event
from sqlalchemy.engine import Connection
from sqlalchemy.sql import FromClause

__all__ = (
    "CLOCK_EVENT_UPDATE_ERROR",
    "CLOCK_EVENT_UPDATE_TRIGGER",
    "clock_event_update_trigger_sql",
    "create_clock_event_update_trigger",
    "drop_clock_event_update_trigger",
    "drop_clock_event_update_trigger_sql",
    "register_clock_event_immutability",
)

CLOCK_EVENT_UPDATE_TRIGGER = "trg_clock_events_immutable_update"
"""Name of the SQLite trigger that protects recorded clock events."""

CLOCK_EVENT_UPDATE_ERROR = (
    "clock_events are immutable; insert a replacement event instead"
)
"""Stable database error raised when a caller tries to rewrite a punch."""


def clock_event_update_trigger_sql() -> str:
    """Return the canonical DDL that rejects updates to ``clock_events``.

    Deletion is deliberately outside this trigger.  Foreign keys already stop
    a referenced audit event being removed, while an unreferenced event can be
    speculative (a writer that lost a race) or part of a deliberate demo-data
    reset.  Such rows have no session history to preserve.
    """
    return f"""
CREATE TRIGGER {CLOCK_EVENT_UPDATE_TRIGGER}
BEFORE UPDATE ON clock_events
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, '{CLOCK_EVENT_UPDATE_ERROR}');
END
""".strip()


def drop_clock_event_update_trigger_sql() -> str:
    """Return DDL that removes the clock-event update guard if it exists."""
    return f"DROP TRIGGER IF EXISTS {CLOCK_EVENT_UPDATE_TRIGGER}"


def create_clock_event_update_trigger(
    _table: Table, connection: Connection, **_options: object
) -> None:
    """Create the update guard after SQLAlchemy creates ``clock_events``."""
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(clock_event_update_trigger_sql())


def drop_clock_event_update_trigger(
    _table: Table, connection: Connection, **_options: object
) -> None:
    """Remove the update guard before SQLAlchemy drops ``clock_events``."""
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(drop_clock_event_update_trigger_sql())


def register_clock_event_immutability(table: FromClause) -> None:
    """Attach the canonical SQLite trigger lifecycle to ``table``.

    The dialect predicate keeps the declarative model portable: databases that
    can express immutable rows differently do not receive SQLite-only syntax.
    """
    if not isinstance(table, Table):
        message = "clock-event immutability must be registered on a Table"
        raise TypeError(message)
    event.listen(table, "after_create", create_clock_event_update_trigger)
    event.listen(table, "before_drop", drop_clock_event_update_trigger)
