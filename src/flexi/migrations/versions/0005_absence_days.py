"""Absence day table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "absence_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False, unique=True),
        sa.Column(
            "absence_type",
            sa.Enum("ANNUAL", "SICK", "FLEXI", name="absencetype"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("absence_days")
