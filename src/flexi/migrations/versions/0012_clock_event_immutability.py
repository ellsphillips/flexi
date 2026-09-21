"""Reject updates to recorded clock events.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-27

Clock events are the append-only facts behind work sessions: a correction adds
replacement events and voids the old session. Referenced rows are already
protected from deletion by foreign keys. Unreferenced rows stay deletable, for
a losing concurrent writer discarding a speculative event and for demo-data
reset.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The DDL is copied, not imported: a migration must not change when the model does.
CLOCK_EVENT_UPDATE_TRIGGER_SQL = (
    "CREATE TRIGGER trg_clock_events_immutable_update\n"
    "BEFORE UPDATE ON clock_events\n"
    "FOR EACH ROW\n"
    "BEGIN\n"
    "    SELECT RAISE(ABORT, "
    "'clock_events are immutable; insert a replacement event instead'"
    ");\n"
    "END"
)
DROP_CLOCK_EVENT_UPDATE_TRIGGER_SQL = (
    "DROP TRIGGER IF EXISTS trg_clock_events_immutable_update"
)


def upgrade() -> None:
    """Install the clock-event update guard without rewriting any row."""
    op.execute(CLOCK_EVENT_UPDATE_TRIGGER_SQL)


def downgrade() -> None:
    """Remove the update guard while leaving every clock event intact."""
    op.execute(DROP_CLOCK_EVENT_UPDATE_TRIGGER_SQL)
