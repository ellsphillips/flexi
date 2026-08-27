"""Clock event and work session tables.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clock_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "action",
            sa.Enum("IN", "OUT", name="clockaction"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="user"),
    )

    op.create_table(
        "work_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "clock_in_id",
            sa.Integer(),
            sa.ForeignKey("clock_events.id"),
            nullable=False,
        ),
        sa.Column(
            "clock_out_id",
            sa.Integer(),
            sa.ForeignKey("clock_events.id"),
            nullable=True,
        ),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column(
            "auto_closed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_table("work_sessions")
    op.drop_table("clock_events")
