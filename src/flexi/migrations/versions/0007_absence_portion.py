"""Half-day absences, notes, and the two new absence types.

Three changes that have to happen together:

* ``portion`` — an absence now covers a full day, a morning or an afternoon.
* ``note`` — required for ``OTHER``, optional elsewhere.
* the uniqueness rule moves from ``date`` to ``(date, portion)``, so a sick
  morning and an annual afternoon can share a date.

The table is rebuilt rather than altered because the v1 schema put ``UNIQUE`` on
the ``date`` column itself, and SQLite cannot drop a column constraint in place.
Every existing row becomes a ``FULL`` day, which is what it already was.

``UNPAID`` and ``OTHER`` join the ``absence_type`` enum. SQLAlchemy renders an
``Enum`` on SQLite as a plain ``VARCHAR`` with no check constraint, so widening
it needs no storage change — but the rebuild below writes the new column
definition out anyway, so the schema and the model agree on paper as well as in
practice.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-08

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ABSENCE_TYPES = ("ANNUAL", "SICK", "FLEXI", "UNPAID", "OTHER")
LEGACY_TYPES = ("ANNUAL", "SICK", "FLEXI")
PORTIONS = ("FULL", "AM", "PM")


def upgrade() -> None:
    op.rename_table("absence_days", "absence_days_old")
    op.create_table(
        "absence_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "absence_type",
            sa.Enum(*ABSENCE_TYPES, name="absencetype"),
            nullable=False,
        ),
        sa.Column(
            "portion",
            sa.Enum(*PORTIONS, name="portion"),
            nullable=False,
            server_default="FULL",
        ),
        sa.Column("note", sa.String(200), nullable=True),
        sa.UniqueConstraint("date", "portion", name="uq_date_portion"),
    )
    op.execute(
        "INSERT INTO absence_days (id, date, absence_type, portion, note) "
        "SELECT id, date, absence_type, 'FULL', NULL FROM absence_days_old"
    )
    op.drop_table("absence_days_old")


def downgrade() -> None:
    """Rebuild the v1 table, keeping only what it can represent.

    Half days and the two new types have nowhere to go in the old schema. They
    are dropped rather than coerced, because a morning of sickness silently
    becoming a whole day off is a worse outcome than losing the row — and a
    downgrade is a deliberate act, not an accident.
    """
    op.rename_table("absence_days", "absence_days_new")
    op.create_table(
        "absence_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False, unique=True),
        sa.Column(
            "absence_type",
            sa.Enum(*LEGACY_TYPES, name="absencetype"),
            nullable=False,
        ),
    )
    op.execute(
        "INSERT INTO absence_days (id, date, absence_type) "
        "SELECT id, date, absence_type FROM absence_days_new "
        "WHERE portion = 'FULL' AND absence_type IN ('ANNUAL', 'SICK', 'FLEXI')"
    )
    op.drop_table("absence_days_new")
