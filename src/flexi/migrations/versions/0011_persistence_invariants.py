"""Enforce singleton and mutually exclusive persistence states.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-27

The service layer already intends these rules, but a second session, an
integration, or a manual repair can bypass it. SQLite partial unique indexes
make the work-session and absence rules atomic at the write boundary. Settings
uses a checked constant key: uniqueness admits one row and the check prevents a
second row from selecting another key.

Legacy conflicts are not repairable without choosing which user record wins.
The migration therefore validates every rule before changing the schema and
fails with all conflicts listed. Valid legacy rows are copied unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection
from sqlalchemy.sql import Select

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS_SINGLETON_KEY = 1

SETTINGS = sa.table("settings", sa.column("id", sa.Integer))
WORK_SESSIONS = sa.table(
    "work_sessions",
    sa.column("id", sa.Integer),
    sa.column("clock_out_id", sa.Integer),
    sa.column("voided", sa.Boolean),
)
ABSENCE_DAYS = sa.table(
    "absence_days",
    sa.column("date", sa.Date),
    sa.column("portion", sa.String),
)


def scalar_count(connection: Connection, statement: Select[tuple[int]]) -> int:
    """Return a count query's non-null integer value."""
    return int(connection.scalar(statement) or 0)


def legacy_conflicts(connection: Connection) -> tuple[str, ...]:
    """Describe states for which enforcing an invariant would discard data."""
    conflicts: list[str] = []

    settings_count = scalar_count(
        connection, sa.select(sa.func.count()).select_from(SETTINGS)
    )
    if settings_count > 1:
        conflicts.append(f"settings has {settings_count} rows (expected at most one)")

    open_session_count = scalar_count(
        connection,
        sa.select(sa.func.count())
        .select_from(WORK_SESSIONS)
        .where(
            WORK_SESSIONS.c.clock_out_id.is_(None),
            WORK_SESSIONS.c.voided.is_(False),
        ),
    )
    if open_session_count > 1:
        conflicts.append(
            f"work_sessions has {open_session_count} non-voided open rows "
            "(expected at most one)"
        )

    conflicting_dates = connection.scalars(
        sa.select(ABSENCE_DAYS.c.date)
        .where(ABSENCE_DAYS.c.portion.in_(("FULL", "AM", "PM")))
        .group_by(ABSENCE_DAYS.c.date)
        .having(
            sa.func.sum(sa.case((ABSENCE_DAYS.c.portion == "FULL", 1), else_=0)) > 0,
            sa.func.count() > 1,
        )
        .order_by(ABSENCE_DAYS.c.date)
    ).all()
    if conflicting_dates:
        dates = ", ".join(str(value) for value in conflicting_dates)
        conflicts.append(f"absence_days mixes FULL with a half on: {dates}")

    return tuple(conflicts)


def validate_legacy_state(connection: Connection) -> None:
    """Refuse an ambiguous migration before performing any schema change."""
    conflicts = legacy_conflicts(connection)
    if conflicts:
        details = "; ".join(conflicts)
        message = (
            "migration 0011 cannot enforce persistence invariants without "
            f"discarding data: {details}. Resolve these rows and retry."
        )
        raise RuntimeError(message)


def upgrade() -> None:
    connection = op.get_bind()
    validate_legacy_state(connection)

    with op.batch_alter_table("settings") as batch:
        batch.add_column(
            sa.Column(
                "singleton_key",
                sa.Integer(),
                nullable=False,
                server_default=sa.text(str(SETTINGS_SINGLETON_KEY)),
            )
        )
        batch.create_check_constraint(
            "ck_settings_singleton_key",
            f"singleton_key = {SETTINGS_SINGLETON_KEY}",
        )
        batch.create_unique_constraint("uq_settings_singleton_key", ("singleton_key",))

    op.create_index(
        "uq_work_sessions_one_open",
        "work_sessions",
        ("voided",),
        unique=True,
        sqlite_where=sa.text("clock_out_id IS NULL AND voided = 0"),
    )
    op.create_index(
        "uq_absence_date_full_am",
        "absence_days",
        ("date",),
        unique=True,
        sqlite_where=sa.text("portion IN ('FULL', 'AM')"),
    )
    op.create_index(
        "uq_absence_date_full_pm",
        "absence_days",
        ("date",),
        unique=True,
        sqlite_where=sa.text("portion IN ('FULL', 'PM')"),
    )


def downgrade() -> None:
    op.drop_index("uq_absence_date_full_pm", table_name="absence_days")
    op.drop_index("uq_absence_date_full_am", table_name="absence_days")
    op.drop_index("uq_work_sessions_one_open", table_name="work_sessions")

    with op.batch_alter_table("settings") as batch:
        batch.drop_constraint("uq_settings_singleton_key", type_="unique")
        batch.drop_constraint("ck_settings_singleton_key", type_="check")
        batch.drop_column("singleton_key")
