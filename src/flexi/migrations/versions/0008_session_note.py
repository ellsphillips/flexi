"""notes and voiding on work sessions

Clock events are immutable, so correcting a session cannot edit one. A
correction inserts a replacement pair and marks the original ``voided``, which
keeps the audit trail intact and keeps the balance honest about what was
actually recorded versus what was later decided.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
