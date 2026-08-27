"""Clock-event audit rows are append-only in every supported schema."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError

from flexi.constants import ClockAction, EventSource
from flexi.models.database.db import Base, ClockEvent, WorkSession
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.invariants import (
    CLOCK_EVENT_UPDATE_ERROR,
    CLOCK_EVENT_UPDATE_TRIGGER,
    clock_event_update_trigger_sql,
)
from flexi.models.database.migrate import alembic_config, run_migrations

SchemaSource = Literal["metadata", "migrations"]

FROZEN_0012_UPDATE_ERROR = (
    "clock_events are immutable; insert a replacement event instead"
)
FROZEN_0012_TRIGGER_SQL = (
    "CREATE TRIGGER trg_clock_events_immutable_update\n"
    "BEFORE UPDATE ON clock_events\n"
    "FOR EACH ROW\n"
    "BEGIN\n"
    "    SELECT RAISE(ABORT, "
    "'clock_events are immutable; insert a replacement event instead'"
    ");\n"
    "END"
)


@pytest.fixture(params=("metadata", "migrations"))
def clock_event_engine(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[Engine]:
    """Build a database through each public schema-construction path."""
    source = cast("SchemaSource", request.param)
    path = tmp_path / f"clock-events-{source}.db"
    if source == "migrations":
        run_migrations(path)
    engine = create_db_engine(path)
    if source == "metadata":
        Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def punch(*, at: datetime = datetime(2026, 8, 27, 9)) -> ClockEvent:
    """Return one complete user punch without going through a service."""
    return ClockEvent(
        action=ClockAction.IN,
        timestamp=at,
        utc_offset_minutes=60,
        source=EventSource.USER,
    )


def trigger_sql(engine: Engine) -> str | None:
    """Return SQLite's stored definition of the immutability trigger."""
    with engine.connect() as connection:
        definition = connection.scalar(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name"
            ),
            {"name": CLOCK_EVENT_UPDATE_TRIGGER},
        )
    return definition if isinstance(definition, str) else None


def normalise_sql(statement: str) -> str:
    """Ignore insignificant DDL whitespace when comparing SQLite metadata."""
    return " ".join(statement.split()).removesuffix(";")


def test_both_schema_paths_install_the_canonical_trigger(
    clock_event_engine: Engine,
) -> None:
    """Fixtures and production migrations execute one shared DDL definition."""
    installed = trigger_sql(clock_event_engine)
    assert installed is not None
    assert normalise_sql(installed) == normalise_sql(clock_event_update_trigger_sql())


def test_orm_cannot_rewrite_a_recorded_clock_event(
    clock_event_engine: Engine,
) -> None:
    """A normal mapped-object mutation fails and leaves the original fact."""
    original = datetime(2026, 8, 27, 9)
    with get_session(clock_event_engine) as session:
        event = punch(at=original)
        session.add(event)
        session.commit()
        event_id = event.id

        event.timestamp = datetime(2026, 8, 27, 10)
        with pytest.raises(IntegrityError, match=re.escape(CLOCK_EVENT_UPDATE_ERROR)):
            session.commit()
        session.rollback()

        stored = session.scalar(
            select(ClockEvent.timestamp).where(ClockEvent.id == event_id)
        )
        assert stored == original


def test_raw_sql_cannot_bypass_clock_event_immutability(
    clock_event_engine: Engine,
) -> None:
    """The invariant lives in SQLite, beneath the ORM and service layers."""
    original = datetime(2026, 8, 27, 9)
    with get_session(clock_event_engine) as session:
        event = punch(at=original)
        session.add(event)
        session.commit()
        event_id = event.id

    with clock_event_engine.connect() as connection:
        with pytest.raises(IntegrityError, match=re.escape(CLOCK_EVENT_UPDATE_ERROR)):
            connection.execute(
                sa.text(
                    "UPDATE clock_events "
                    "SET timestamp = '2026-08-27 10:00:00' WHERE id = :event_id"
                ),
                {"event_id": event_id},
            )
        connection.rollback()

    with get_session(clock_event_engine) as session:
        stored = session.scalar(
            select(ClockEvent.timestamp).where(ClockEvent.id == event_id)
        )
        assert stored == original


def test_deletion_distinguishes_unreferenced_from_audit_events(
    clock_event_engine: Engine,
) -> None:
    """Speculative rows are removable; a session's referenced facts are not."""
    with get_session(clock_event_engine) as session:
        speculative = punch()
        session.add(speculative)
        session.commit()
        speculative_id = speculative.id

        session.delete(speculative)
        session.commit()
        assert session.get(ClockEvent, speculative_id) is None

        recorded = punch(at=datetime(2026, 8, 27, 10))
        session.add(recorded)
        session.flush()
        session.add(
            WorkSession(
                clock_in_id=recorded.id,
                work_date=date(2026, 8, 27),
            )
        )
        session.commit()
        recorded_id = recorded.id

        session.delete(recorded)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert session.get(ClockEvent, recorded_id) is not None


def upgrade(path: Path, target: str) -> None:
    """Upgrade ``path`` through Alembic's real script environment."""
    with alembic_config(path) as config:
        command.upgrade(config, target)


def downgrade(path: Path, target: str) -> None:
    """Downgrade ``path`` through Alembic's real script environment."""
    with alembic_config(path) as config:
        command.downgrade(config, target)


def update_clock_event(engine: Engine, timestamp: str) -> None:
    """Attempt one direct rewrite and commit it when the schema permits."""
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE clock_events SET timestamp = :timestamp WHERE id = 1"),
            {"timestamp": timestamp},
        )


def test_migration_upgrade_and_downgrade_toggle_only_the_guard(
    tmp_path: Path,
) -> None:
    """0012 is reversible, repeatable, and never rewrites the protected row."""
    path = tmp_path / "clock-event-migration.db"
    upgrade(path, "0011")
    engine = create_db_engine(path)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO clock_events"
                " (id, action, timestamp, utc_offset_minutes, source)"
                " VALUES (1, 'IN', '2026-08-27 09:00:00', 60, 'user')"
            )
        )
    update_clock_event(engine, "2026-08-27 09:30:00")
    engine.dispose()

    upgrade(path, "0012")
    engine = create_db_engine(path)
    assert trigger_sql(engine) is not None
    with pytest.raises(IntegrityError, match=re.escape(CLOCK_EVENT_UPDATE_ERROR)):
        update_clock_event(engine, "2026-08-27 10:00:00")
    engine.dispose()

    downgrade(path, "0011")
    engine = create_db_engine(path)
    assert trigger_sql(engine) is None
    update_clock_event(engine, "2026-08-27 10:30:00")
    engine.dispose()

    upgrade(path, "0012")
    engine = create_db_engine(path)
    try:
        assert trigger_sql(engine) is not None
        with engine.connect() as connection:
            stored = connection.scalar(
                sa.text("SELECT timestamp FROM clock_events WHERE id = 1")
            )
        assert stored == "2026-08-27 10:30:00"
    finally:
        engine.dispose()


def test_migration_0012_owns_frozen_ddl_and_error_semantics(tmp_path: Path) -> None:
    """A live helper refactor cannot retroactively change revision 0012."""
    migration = (
        Path(__file__).parents[3]
        / "src/flexi/migrations/versions/0012_clock_event_immutability.py"
    )
    assert "flexi.models.database.invariants" not in migration.read_text(
        encoding="utf-8"
    )

    path = tmp_path / "frozen-clock-event-migration.db"
    upgrade(path, "0011")
    engine = create_db_engine(path)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO clock_events"
                " (id, action, timestamp, utc_offset_minutes, source)"
                " VALUES (1, 'IN', '2026-08-27 09:00:00', 60, 'user')"
            )
        )
    engine.dispose()

    upgrade(path, "0012")
    engine = create_db_engine(path)
    try:
        installed = trigger_sql(engine)
        assert installed is not None
        assert normalise_sql(installed) == normalise_sql(FROZEN_0012_TRIGGER_SQL)
        with pytest.raises(IntegrityError, match=re.escape(FROZEN_0012_UPDATE_ERROR)):
            update_clock_event(engine, "2026-08-27 10:00:00")
    finally:
        engine.dispose()
