"""Represent complete bank-holiday refreshes independently of their events.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-27

The legacy cache repeated one fetch timestamp on every event.  That made a
valid empty response impossible to distinguish from no response at all.  One
row per division now records the complete refresh, and event rows reference it.
Existing divisions are backfilled with their most recent legacy timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BANK_HOLIDAY_CACHE = sa.table(
    "bank_holiday_cache",
    sa.column("division", sa.String(30)),
    sa.column("fetched_at", sa.DateTime()),
)
_BANK_HOLIDAY_REFRESHES = sa.table(
    "bank_holiday_refreshes",
    sa.column("division", sa.String(30)),
    sa.column("fetched_at", sa.DateTime()),
)


def upgrade() -> None:
    """Create refresh metadata and move legacy per-event timestamps into it."""
    op.create_table(
        "bank_holiday_refreshes",
        sa.Column("division", sa.String(30), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )
    latest_refreshes = (
        sa.select(
            _BANK_HOLIDAY_CACHE.c.division,
            sa.func.max(_BANK_HOLIDAY_CACHE.c.fetched_at).label("fetched_at"),
        )
        .group_by(_BANK_HOLIDAY_CACHE.c.division)
        .order_by(_BANK_HOLIDAY_CACHE.c.division)
    )
    op.get_bind().execute(
        sa.insert(_BANK_HOLIDAY_REFRESHES).from_select(
            ("division", "fetched_at"), latest_refreshes
        )
    )

    with op.batch_alter_table("bank_holiday_cache") as batch:
        batch.drop_column("fetched_at")
        batch.create_foreign_key(
            "fk_bank_holiday_cache_division_refresh",
            "bank_holiday_refreshes",
            ["division"],
            ["division"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """Restore each event's refresh timestamp before removing the metadata."""
    with op.batch_alter_table("bank_holiday_cache") as batch:
        batch.drop_constraint(
            "fk_bank_holiday_cache_division_refresh", type_="foreignkey"
        )
        batch.add_column(sa.Column("fetched_at", sa.DateTime(), nullable=True))

    refresh_time = (
        sa.select(_BANK_HOLIDAY_REFRESHES.c.fetched_at)
        .where(_BANK_HOLIDAY_REFRESHES.c.division == _BANK_HOLIDAY_CACHE.c.division)
        .scalar_subquery()
    )
    op.get_bind().execute(
        sa.update(_BANK_HOLIDAY_CACHE).values(fetched_at=refresh_time)
    )

    with op.batch_alter_table("bank_holiday_cache") as batch:
        batch.alter_column("fetched_at", existing_type=sa.DateTime(), nullable=False)
    op.drop_table("bank_holiday_refreshes")
