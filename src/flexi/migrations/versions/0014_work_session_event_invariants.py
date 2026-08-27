"""Bind each clock event to one correctly oriented work-session role.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-27

A clock event is an immutable fact.  Reusing one fact in multiple sessions or
using an OUT event as a start (and vice versa) gives that fact contradictory
meanings.  Unique constraints make ownership atomic; SQLite triggers enforce
the action stored in the referenced row.

Legacy conflicts cannot be repaired without inventing user history.  Every
conflict is therefore detected before the first schema change, and the
migration names the rows a person must resolve.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORK_SESSIONS = sa.table(
    "work_sessions",
    sa.column("id", sa.Integer),
    sa.column("clock_in_id", sa.Integer),
    sa.column("clock_out_id", sa.Integer),
)
CLOCK_EVENTS = sa.table(
    "clock_events",
    sa.column("id", sa.Integer),
    sa.column("action", sa.String),
)

# These statements are deliberately frozen in the migration rather than
# imported from the runtime invariant module.  Historical migrations must keep
# the exact behaviour released with their revision.
WORK_SESSION_ACTION_INSERT_TRIGGER_SQL = """
CREATE TRIGGER trg_work_sessions_clock_actions_insert
BEFORE INSERT ON work_sessions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'work_sessions.clock_in_id must reference an IN clock event')
    WHERE (
        SELECT action FROM clock_events WHERE id = NEW.clock_in_id
    ) IS NOT 'IN';
    SELECT RAISE(ABORT, 'work_sessions.clock_out_id must reference an OUT clock event')
    WHERE NEW.clock_out_id IS NOT NULL
      AND (
          SELECT action FROM clock_events WHERE id = NEW.clock_out_id
      ) IS NOT 'OUT';
END
""".strip()
WORK_SESSION_ACTION_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER trg_work_sessions_clock_actions_update
BEFORE UPDATE ON work_sessions
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'work_sessions.clock_in_id must reference an IN clock event')
    WHERE (
        SELECT action FROM clock_events WHERE id = NEW.clock_in_id
    ) IS NOT 'IN';
    SELECT RAISE(ABORT, 'work_sessions.clock_out_id must reference an OUT clock event')
    WHERE NEW.clock_out_id IS NOT NULL
      AND (
          SELECT action FROM clock_events WHERE id = NEW.clock_out_id
      ) IS NOT 'OUT';
END
""".strip()
DROP_WORK_SESSION_ACTION_INSERT_TRIGGER_SQL = (
    "DROP TRIGGER IF EXISTS trg_work_sessions_clock_actions_insert"
)
DROP_WORK_SESSION_ACTION_UPDATE_TRIGGER_SQL = (
    "DROP TRIGGER IF EXISTS trg_work_sessions_clock_actions_update"
)


def legacy_conflicts(connection: Connection) -> tuple[str, ...]:
    """Describe event ownership or role states that cannot be inferred away."""
    conflicts: list[str] = []

    duplicate_clock_ins = connection.scalars(
        sa.select(WORK_SESSIONS.c.clock_in_id)
        .group_by(WORK_SESSIONS.c.clock_in_id)
        .having(sa.func.count() > 1)
        .order_by(WORK_SESSIONS.c.clock_in_id)
    ).all()
    if duplicate_clock_ins:
        event_ids = ", ".join(str(value) for value in duplicate_clock_ins)
        conflicts.append(f"work_sessions reuses clock_in_id values: {event_ids}")

    duplicate_clock_outs = connection.scalars(
        sa.select(WORK_SESSIONS.c.clock_out_id)
        .where(WORK_SESSIONS.c.clock_out_id.is_not(None))
        .group_by(WORK_SESSIONS.c.clock_out_id)
        .having(sa.func.count() > 1)
        .order_by(WORK_SESSIONS.c.clock_out_id)
    ).all()
    if duplicate_clock_outs:
        event_ids = ", ".join(str(value) for value in duplicate_clock_outs)
        conflicts.append(f"work_sessions reuses clock_out_id values: {event_ids}")

    clock_in_events = CLOCK_EVENTS.alias("clock_in_events")
    mismatched_clock_ins = connection.scalars(
        sa.select(WORK_SESSIONS.c.id)
        .select_from(
            WORK_SESSIONS.outerjoin(
                clock_in_events,
                clock_in_events.c.id == WORK_SESSIONS.c.clock_in_id,
            )
        )
        .where(
            sa.or_(
                clock_in_events.c.id.is_(None),
                clock_in_events.c.action != "IN",
            )
        )
        .order_by(WORK_SESSIONS.c.id)
    ).all()
    if mismatched_clock_ins:
        row_ids = ", ".join(str(value) for value in mismatched_clock_ins)
        conflicts.append(f"work_sessions has non-IN clock_in rows: {row_ids}")

    clock_out_events = CLOCK_EVENTS.alias("clock_out_events")
    mismatched_clock_outs = connection.scalars(
        sa.select(WORK_SESSIONS.c.id)
        .select_from(
            WORK_SESSIONS.outerjoin(
                clock_out_events,
                clock_out_events.c.id == WORK_SESSIONS.c.clock_out_id,
            )
        )
        .where(
            WORK_SESSIONS.c.clock_out_id.is_not(None),
            sa.or_(
                clock_out_events.c.id.is_(None),
                clock_out_events.c.action != "OUT",
            ),
        )
        .order_by(WORK_SESSIONS.c.id)
    ).all()
    if mismatched_clock_outs:
        row_ids = ", ".join(str(value) for value in mismatched_clock_outs)
        conflicts.append(f"work_sessions has non-OUT clock_out rows: {row_ids}")

    return tuple(conflicts)


def validate_legacy_state(connection: Connection) -> None:
    """Refuse an ambiguous migration before performing any schema change."""
    conflicts = legacy_conflicts(connection)
    if conflicts:
        details = "; ".join(conflicts)
        message = (
            "migration 0014 cannot enforce clock-session invariants without "
            f"discarding data: {details}. Resolve these rows and retry."
        )
        raise RuntimeError(message)


def upgrade() -> None:
    """Validate legacy history, then enforce ownership and event roles."""
    validate_legacy_state(op.get_bind())

    with op.batch_alter_table("work_sessions") as batch:
        batch.create_unique_constraint("uq_work_sessions_clock_in_id", ["clock_in_id"])
        batch.create_unique_constraint(
            "uq_work_sessions_clock_out_id", ["clock_out_id"]
        )

    op.execute(WORK_SESSION_ACTION_INSERT_TRIGGER_SQL)
    op.execute(WORK_SESSION_ACTION_UPDATE_TRIGGER_SQL)


def downgrade() -> None:
    """Remove event-role and ownership guards without rewriting history."""
    op.execute(DROP_WORK_SESSION_ACTION_UPDATE_TRIGGER_SQL)
    op.execute(DROP_WORK_SESSION_ACTION_INSERT_TRIGGER_SQL)

    with op.batch_alter_table("work_sessions") as batch:
        batch.drop_constraint("uq_work_sessions_clock_out_id", type_="unique")
        batch.drop_constraint("uq_work_sessions_clock_in_id", type_="unique")
