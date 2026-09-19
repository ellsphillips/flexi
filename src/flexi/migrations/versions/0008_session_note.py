"""Notes and voiding on work sessions.

Clock events are immutable, so a correction cannot edit one. It inserts a
replacement pair and marks the original ``voided``, so the audit trail holds
both what was recorded and what was later decided.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-08

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("work_sessions", sa.Column("note", sa.String(200), nullable=True))
    op.add_column(
        "work_sessions",
        sa.Column("voided", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    with op.batch_alter_table("work_sessions") as batch:
        batch.drop_column("voided")
        batch.drop_column("note")
