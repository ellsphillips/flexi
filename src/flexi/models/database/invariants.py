"""Reusable database DDL for invariants SQLAlchemy cannot model directly.

SQLite cannot express immutable rows or a foreign row's required value with a
table constraint.  Triggers enforce those rules for metadata-created schemas.
Historical migrations deliberately carry frozen copies of the DDL instead of
importing this evolving runtime module; regression tests keep the two schema
construction paths equivalent.
"""

from __future__ import annotations

from typing import Literal, assert_never

from sqlalchemy import Table, event
from sqlalchemy.engine import Connection
from sqlalchemy.sql import FromClause

__all__ = (
    "CLOCK_EVENT_UPDATE_ERROR",
    "CLOCK_EVENT_UPDATE_TRIGGER",
    "WORK_SESSION_ACTION_INSERT_TRIGGER",
    "WORK_SESSION_ACTION_UPDATE_TRIGGER",
    "WORK_SESSION_CLOCK_IN_ACTION_ERROR",
    "WORK_SESSION_CLOCK_OUT_ACTION_ERROR",
    "clock_event_update_trigger_sql",
    "create_clock_event_update_trigger",
    "create_work_session_action_triggers",
    "drop_clock_event_update_trigger",
    "drop_clock_event_update_trigger_sql",
    "drop_work_session_action_trigger_sql",
    "drop_work_session_action_triggers",
    "register_clock_event_immutability",
    "register_work_session_action_invariants",
    "work_session_action_trigger_name",
    "work_session_action_trigger_sql",
)

CLOCK_EVENT_UPDATE_TRIGGER = "trg_clock_events_immutable_update"
"""Name of the SQLite trigger that protects recorded clock events."""

CLOCK_EVENT_UPDATE_ERROR = (
    "clock_events are immutable; insert a replacement event instead"
)
"""Stable database error raised when a caller tries to rewrite a punch."""

WORK_SESSION_ACTION_INSERT_TRIGGER = "trg_work_sessions_clock_actions_insert"
"""Name of the trigger that validates a newly linked pair of clock events."""

WORK_SESSION_ACTION_UPDATE_TRIGGER = "trg_work_sessions_clock_actions_update"
"""Name of the trigger that validates changed work-session event links."""

WORK_SESSION_CLOCK_IN_ACTION_ERROR = (
    "work_sessions.clock_in_id must reference an IN clock event"
)
"""Stable database error for a clock-out event used as a session start."""

WORK_SESSION_CLOCK_OUT_ACTION_ERROR = (
    "work_sessions.clock_out_id must reference an OUT clock event"
)
"""Stable database error for a clock-in event used as a session finish."""


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


def work_session_action_trigger_name(
    operation: Literal["INSERT", "UPDATE"],
) -> str:
    """Return the stable trigger name for one work-session write operation."""
    if operation == "INSERT":
        return WORK_SESSION_ACTION_INSERT_TRIGGER
    if operation == "UPDATE":
        return WORK_SESSION_ACTION_UPDATE_TRIGGER
    assert_never(operation)


def work_session_action_trigger_sql(
    operation: Literal["INSERT", "UPDATE"],
) -> str:
    """Return DDL that requires event actions to match their session roles."""
    trigger = work_session_action_trigger_name(operation)
    statement = f"""
CREATE TRIGGER {trigger}
BEFORE {operation} ON work_sessions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, '{WORK_SESSION_CLOCK_IN_ACTION_ERROR}')
    WHERE (
        SELECT action FROM clock_events WHERE id = NEW.clock_in_id
    ) IS NOT 'IN';
    SELECT RAISE(ABORT, '{WORK_SESSION_CLOCK_OUT_ACTION_ERROR}')
    WHERE NEW.clock_out_id IS NOT NULL
      AND (
          SELECT action FROM clock_events WHERE id = NEW.clock_out_id
      ) IS NOT 'OUT';
END
"""  # noqa: S608 - every interpolated value is a closed module constant
    return statement.strip()


def drop_work_session_action_trigger_sql(
    operation: Literal["INSERT", "UPDATE"],
) -> str:
    """Return DDL that removes one event-role guard if it exists."""
    return f"DROP TRIGGER IF EXISTS {work_session_action_trigger_name(operation)}"


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


def create_work_session_action_triggers(
    _table: Table, connection: Connection, **_options: object
) -> None:
    """Create both event-role guards after SQLAlchemy creates work sessions."""
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(work_session_action_trigger_sql("INSERT"))
        connection.exec_driver_sql(work_session_action_trigger_sql("UPDATE"))


def drop_work_session_action_triggers(
    _table: Table, connection: Connection, **_options: object
) -> None:
    """Remove both event-role guards before SQLAlchemy drops work sessions."""
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(drop_work_session_action_trigger_sql("UPDATE"))
        connection.exec_driver_sql(drop_work_session_action_trigger_sql("INSERT"))


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


def register_work_session_action_invariants(table: FromClause) -> None:
    """Attach the SQLite event-role trigger lifecycle to ``work_sessions``."""
    if not isinstance(table, Table):
        message = "work-session action invariants must be registered on a Table"
        raise TypeError(message)
    event.listen(table, "after_create", create_work_session_action_triggers)
    event.listen(table, "before_drop", drop_work_session_action_triggers)
