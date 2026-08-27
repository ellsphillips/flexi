"""Balance adjustments.

A flexi balance is derived, never stored: it is worked minus expected,
accumulated over the leave year. That is the right model until the day somebody
needs to draw a line under a period they never tracked — and then there is
nothing to edit, because there is nothing stored.

An adjustment is the missing piece. A signed number of minutes, an effective
date, and a reason. It is counted like any other term in the sum, so the balance
stays derived, the clock events stay immutable, and drawing the line is one row
that can be read, explained and removed.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "balance_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False, index=True),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("balance_adjustments")
