"""Remember a GOV.UK fetch that came back with nothing.

Every command opens the database, and opening it fills a calendar that is not
there. Offline, or behind a proxy that refuses, that is the whole fetch budget
in front of `flexi clock in`, once per command, all day.

A separate table from `bank_holiday_refreshes` because an attempt that failed is
not a calendar. A row in that one says a division was fetched in full, and
writing a failure into it would make an install with no holidays at all read as
one whose year happens to have none.

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
