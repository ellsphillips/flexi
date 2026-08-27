"""Settings and leave entitlement tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("leave_year_start", sa.String(5), nullable=False),
        sa.Column("working_days", sa.String(27), nullable=False),
        sa.Column("bank_holiday_division", sa.String(30), nullable=False),
        sa.Column("auto_close_time", sa.String(5), nullable=False),
    )
    op.create_table(
        "leave_entitlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("year", sa.Integer(), nullable=False, unique=True),
        sa.Column("days", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("leave_entitlements")
    op.drop_table("settings")
