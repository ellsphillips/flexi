"""Add `bank_holiday_attempts`: when a division was last fetched in vain.

Separate from `bank_holiday_refreshes`, where a row says a division was fetched
in full. A failure recorded there would make an install with no holidays at all
read as one whose year happens to have none.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-19

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bank_holiday_attempts",
        sa.Column("division", sa.String(30), primary_key=True),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("bank_holiday_attempts")
