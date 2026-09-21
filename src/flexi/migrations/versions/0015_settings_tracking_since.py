"""Record the day Flexi started tracking.

Days before ``tracking_since`` expect no work, so a leave year that opened
months before the install charges no deficit for the days in between.

Backfilled to the earliest clock event or booked absence in the database, which
is the earliest day Flexi can be shown to have been in use; a database with
neither takes the date of this migration. A database carrying a balance
adjustment is dated no later than the opening day of the leave year its earliest
adjustment falls in: `flexi balance zero` sizes that row to absorb the deficit
since then, and untracking any of those days would leave it absorbing a deficit
nothing charges any more.

Nullable, and null means "count every day". Nothing here writes a null; the
column stays nullable so that a row written by an older Flexi against a newer
schema is readable instead of a constraint error.

The date comes from `flexi.wallclock`, the one place Flexi reads the clock,
which makes this the only migration that imports from the application. That
module depends on nothing but the standard library, so it cannot drag a schema
change into an import cycle.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-27

"""

import calendar
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

from flexi import wallclock

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

EARLIEST_SETTLEMENT = sa.text("SELECT MIN(date) FROM balance_adjustments")

LEAVE_YEAR = sa.text("SELECT leave_year_start FROM settings LIMIT 1")


def _clamped(year: int, month: int, day: int) -> date:
    """That day of that month, or the month's last day where it is shorter."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _opening_day(when: date, leave_year_start: str) -> date:
    """The first day of the leave year containing ``when``.

    Restates `flexi.domain.leaveyear`: a frozen revision keeps its own
    arithmetic.
    """
    month, day = (int(part) for part in leave_year_start.split("-"))
    this_year = _clamped(when.year, month, day)
    if when >= this_year:
        return this_year
    return _clamped(when.year - 1, month, day)


def upgrade() -> None:
    op.add_column("settings", sa.Column("tracking_since", sa.Date(), nullable=True))

    connection = op.get_bind()
    leave_year_start = connection.execute(LEAVE_YEAR).scalar()
    if leave_year_start is None:
        return

    earliest = connection.execute(EARLIEST_RECORD).scalar()
    stamp = date.fromisoformat(earliest) if earliest else wallclock.today()
    settled = connection.execute(EARLIEST_SETTLEMENT).scalar()
    if settled is not None:
        opening = _opening_day(date.fromisoformat(settled), str(leave_year_start))
        stamp = min(stamp, opening)

    connection.execute(
        sa.text("UPDATE settings SET tracking_since = :stamp"),
        {"stamp": stamp.isoformat()},
    )


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.drop_column("tracking_since")
