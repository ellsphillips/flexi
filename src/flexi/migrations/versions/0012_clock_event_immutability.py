"""Reject updates to recorded clock events.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-27

Clock events are the append-only facts behind work sessions.  Corrections add
replacement events and void the old session; mutating a punch in place would
silently rewrite the audit trail.  Referenced rows are already protected from
deletion by foreign keys.  Unreferenced rows remain deletable because a losing
concurrent writer may need to discard a speculative event, and demo-data reset
deliberately clears them.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Migration DDL is intentionally frozen here.  Importing a live model helper
# would let a future runtime refactor silently rewrite this historical step.
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
