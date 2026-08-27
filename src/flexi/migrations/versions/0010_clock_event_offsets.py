"""Clock events carry the offset that was in force when they were punched.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-09
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ZONE = "FLEXI_LEGACY_TZ"
LEGACY_CLOCK = "FLEXI_LEGACY_CLOCK"
SYSTEM = "system"
LAST_MINUTE = time(23, 59)

events = sa.table(
    "clock_events",
    sa.column("id", sa.Integer),
    sa.column("timestamp", sa.DateTime),
    sa.column("source", sa.String),
    sa.column("utc_offset_minutes", sa.Integer),
)
sessions = sa.table(
    "work_sessions",
    sa.column("id", sa.Integer),
    sa.column("clock_in_id", sa.Integer),
    sa.column("clock_out_id", sa.Integer),
    sa.column("work_date", sa.Date),
)


def _zone() -> ZoneInfo | None:
    name = os.environ.get(LEGACY_ZONE)
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        msg = f"{LEGACY_ZONE}={name!r} is not a timezone this machine knows"
        raise RuntimeError(msg) from error


def _localise(naive: datetime, zone: ZoneInfo | None) -> datetime:
    if zone is None:
        return naive.astimezone()
    # Pinned to the offset that was in force at that wall time, rather than
    # left carrying the zone: the instant is the same either way, and a fixed
    # offset is what the column stores.
    attached = naive.replace(tzinfo=zone)
    offset = attached.utcoffset()
    if offset is None:
        message = f"{naive!r} has no UTC offset in {zone!s}"
        raise ValueError(message)
    return attached.astimezone(timezone(offset))


def _from_instant(naive_utc: datetime, zone: ZoneInfo | None) -> datetime:
    aware = naive_utc.replace(tzinfo=UTC)
    return aware.astimezone(zone) if zone is not None else aware.astimezone()


def _offset_minutes(aware: datetime) -> int:
    offset = aware.utcoffset()
    if offset is None:
        message = f"{aware!r} has no UTC offset"
        raise ValueError(message)
    return int(offset.total_seconds() // 60)


def upgrade() -> None:
    op.add_column(
        "clock_events", sa.Column("utc_offset_minutes", sa.Integer(), nullable=True)
    )

    zone = _zone()
    already_wall = os.environ.get(LEGACY_CLOCK, "utc").lower() == "wall"
    connection = op.get_bind()

    rows = connection.execute(
        sa.select(events.c.id, events.c.timestamp, events.c.source)
    ).all()
    for row in rows:
        if row.timestamp is None:
            continue
        if row.source == SYSTEM or already_wall:
            aware = _localise(row.timestamp, zone)
        else:
            aware = _from_instant(row.timestamp, zone)
        connection.execute(
            events.update()
            .where(events.c.id == row.id)
            .values(
                timestamp=aware.replace(tzinfo=None),
                utc_offset_minutes=_offset_minutes(aware),
            )
        )

    _repair_auto_closes(zone)


def _repair_auto_closes(zone: ZoneInfo | None) -> None:
    started = events.alias("started")
    ended = events.alias("ended")
    query = (
        sa.select(
            ended.c.id,
            sessions.c.work_date,
            started.c.timestamp.label("opened"),
            ended.c.timestamp.label("closed"),
        )
        .select_from(
            sessions.join(started, started.c.id == sessions.c.clock_in_id).join(
                ended, ended.c.id == sessions.c.clock_out_id
            )
        )
        .where(ended.c.source == SYSTEM)
    )
    connection = op.get_bind()
    inverted = [
        (row.id, row.work_date)
        for row in connection.execute(query).all()
        if row.closed < row.opened
    ]
    for event_id, work_date in inverted:
        aware = _localise(datetime.combine(work_date, LAST_MINUTE), zone)
        connection.execute(
            events.update()
            .where(events.c.id == event_id)
            .values(
                timestamp=aware.replace(tzinfo=None),
                utc_offset_minutes=_offset_minutes(aware),
            )
        )


def downgrade() -> None:
    """Wall a person's rows back to the instant.

    This is lossy for the repeated hour only when the offset column is missing,
    which it is not.
    """
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            events.c.id,
            events.c.timestamp,
            events.c.source,
            events.c.utc_offset_minutes,
        )
    ).all()
    for row in rows:
        if row.source == SYSTEM or row.timestamp is None:
            continue
        offset = row.utc_offset_minutes
        aware = (
            row.timestamp.replace(tzinfo=timezone(timedelta(minutes=offset)))
            if offset is not None
            else _localise(row.timestamp, _zone())
        )
        connection.execute(
            events.update()
            .where(events.c.id == row.id)
            .values(timestamp=aware.astimezone(UTC).replace(tzinfo=None))
        )
    # clock_events is the target of two foreign keys from work_sessions, and
    # SQLite drops a column by rebuilding the table. env.py turns foreign keys
    # on, so the rebuild has to turn them off around itself.
    # Dropped in place. A batch_alter_table rebuild would DROP clock_events,
    # which two foreign keys in work_sessions point at, and SQLite refuses --
    # PRAGMA foreign_keys cannot be changed inside alembic's transaction.
    op.execute("ALTER TABLE clock_events DROP COLUMN utc_offset_minutes")
