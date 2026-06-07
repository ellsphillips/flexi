"""contracted hours and the day window on settings

Adds the three numbers the v1 code held as module constants: how long a standard
day is (``STANDARD_DAY_HOURS = 7.4`` in ``services/wallet.py``) and the span the
punch strip draws. Backfilled to 7h24 and 07:00-19:00, which is what the constant
said, so an existing database keeps the balance it had.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "contracted_minutes", sa.Integer(), nullable=False, server_default="444"
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "day_window_start", sa.String(5), nullable=False, server_default="07:00"
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "day_window_end", sa.String(5), nullable=False, server_default="19:00"
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("day_window_end")
        batch.drop_column("day_window_start")
        batch.drop_column("contracted_minutes")
