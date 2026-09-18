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

A settled balance is the one thing that cannot be dated from its earliest
record. `flexi balance zero` writes a single adjustment sized to absorb the
deficit accumulated since the start of its leave year, counted under the rule
that every day before the install counted. Untracking any of those days would
leave that row absorbing a deficit nothing charges any more, and the balance
reads hundreds of hours in surplus. So a database carrying an adjustment is
dated no later than the opening day of the leave year its earliest one falls in.

Nullable, and null means "count every day", which is what the code did before.
Nothing here can produce a null; the column stays nullable so that a row written
by an older Flexi against a newer schema is readable rather than a constraint
error.

The date comes from `flexi.wallclock`, which makes this the only migration that
imports from the application. That is a deliberate exception: `wallclock` is the
one place Flexi reads the clock, the invariant is worth more than a frozen
revision's usual independence, and the module depends on nothing but the
standard library, so it cannot drag a schema change into an import cycle.

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

    The rule `flexi.domain.leaveyear` states, restated: a frozen revision keeps
    its own arithmetic.
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
