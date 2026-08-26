"""Record the day Flexi started tracking.

A leave year usually starts months before somebody installs Flexi. Every working
day in between has no sessions on it, and until now each one scored a full
contracted day of deficit: an April leave year set up in August opened on a
balance of -762 hours, computed entirely from days the user was never asked
about.

Backfilled to the earliest thing the database has a record of -- a clock event
or a booked absence -- because that is the earliest day Flexi can be shown to
have been in use. A database with neither has never recorded anything, so there
is no deficit worth keeping and it takes the date of this migration.

Nullable, and null means "count every day", which is what the code did before.
Nothing here can produce a null; the column stays nullable so that a row written
by an older Flexi against a newer schema is readable rather than a constraint
error.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-27

"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EARLIEST_RECORD = sa.text("""
    SELECT MIN(seen) FROM (
        SELECT MIN(work_date) AS seen FROM work_sessions
        UNION ALL
        SELECT MIN(date) AS seen FROM absence_days
    )
""")


def upgrade() -> None:
    op.add_column("settings", sa.Column("tracking_since", sa.Date(), nullable=True))

    connection = op.get_bind()
    earliest = connection.execute(EARLIEST_RECORD).scalar()
    stamp = earliest or date.today().isoformat()  # noqa: DTZ011
    connection.execute(
        sa.text("UPDATE settings SET tracking_since = :stamp"), {"stamp": str(stamp)}
    )


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("tracking_since")
