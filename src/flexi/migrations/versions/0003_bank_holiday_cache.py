"""bank holiday cache table

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bank_holiday_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("division", sa.String(30), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("division", "date", name="uq_division_date"),
    )


def downgrade() -> None:
    op.drop_table("bank_holiday_cache")
