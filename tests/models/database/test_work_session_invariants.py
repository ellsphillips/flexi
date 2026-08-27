"""Clock events have one owner and one semantic role in every schema path."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Literal, cast
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection, Engine, Table
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import FromClause

from flexi.constants import ClockAction
from flexi.models.database.db import Base, ClockEvent, WorkSession
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.invariants import (
    CLOCK_EVENT_UPDATE_TRIGGER,
    WORK_SESSION_ACTION_INSERT_TRIGGER,
    WORK_SESSION_ACTION_UPDATE_TRIGGER,
    WORK_SESSION_CLOCK_IN_ACTION_ERROR,
    WORK_SESSION_CLOCK_OUT_ACTION_ERROR,
    create_clock_event_update_trigger,
    create_work_session_action_triggers,
    drop_clock_event_update_trigger,
    drop_work_session_action_triggers,
    register_clock_event_immutability,
    register_work_session_action_invariants,
    work_session_action_trigger_name,
    work_session_action_trigger_sql,
)
from flexi.models.database.migrate import run_migrations

SchemaSource = Literal["metadata", "migrations"]
TriggerOperation = Literal["INSERT", "UPDATE"]


@pytest.fixture(params=("metadata", "migrations"))
def work_session_engine(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[Engine]:
    """Build a database through both public schema-construction paths."""
    source = cast("SchemaSource", request.param)
    path = tmp_path / f"work-session-invariants-{source}.db"
    if source == "migrations":
        run_migrations(path)
    engine = create_db_engine(path)
    if source == "metadata":
        Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def punch(action: ClockAction, *, hour: int) -> ClockEvent:
    """Return a complete event without relying on clock-service validation."""
    return ClockEvent(
        action=action,
        timestamp=datetime(2026, 8, 27, hour),
        utc_offset_minutes=60,
    )


def stored_trigger(engine: Engine, name: str) -> str | None:
    """Return one trigger definition as SQLite stored it."""
    with engine.connect() as connection:
        definition = connection.scalar(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name"
            ),
            {"name": name},
        )
    return definition if isinstance(definition, str) else None


def normalise_sql(statement: str) -> str:
    """Ignore insignificant DDL whitespace in schema comparisons."""
    return " ".join(statement.split()).removesuffix(";")


@pytest.mark.parametrize(
    ("operation", "name"),
    [
        ("INSERT", WORK_SESSION_ACTION_INSERT_TRIGGER),
        ("UPDATE", WORK_SESSION_ACTION_UPDATE_TRIGGER),
    ],
)
def test_both_schema_paths_install_the_canonical_role_triggers(
    work_session_engine: Engine,
    operation: TriggerOperation,
    name: str,
) -> None:
    installed = stored_trigger(work_session_engine, name)
    assert installed is not None
    assert normalise_sql(installed) == normalise_sql(
        work_session_action_trigger_sql(operation)
    )
    assert work_session_action_trigger_name(operation) == name


def test_one_clock_in_event_cannot_start_two_sessions(
    work_session_engine: Engine,
) -> None:
    with get_session(work_session_engine) as session:
        clock_in = punch(ClockAction.IN, hour=9)
        first_out = punch(ClockAction.OUT, hour=10)
        second_out = punch(ClockAction.OUT, hour=11)
        session.add_all((clock_in, first_out, second_out))
        session.flush()
        session.add_all(
            (
                WorkSession(
                    clock_in_id=clock_in.id,
                    clock_out_id=first_out.id,
                    work_date=date(2026, 8, 27),
                ),
                WorkSession(
                    clock_in_id=clock_in.id,
                    clock_out_id=second_out.id,
                    work_date=date(2026, 8, 28),
                ),
            )
        )

        with pytest.raises(IntegrityError, match="work_sessions.clock_in_id"):
            session.commit()


def test_one_clock_out_event_cannot_finish_two_sessions(
    work_session_engine: Engine,
) -> None:
    with get_session(work_session_engine) as session:
        first_in = punch(ClockAction.IN, hour=8)
        second_in = punch(ClockAction.IN, hour=9)
        clock_out = punch(ClockAction.OUT, hour=10)
        session.add_all((first_in, second_in, clock_out))
        session.flush()
        session.add_all(
            (
                WorkSession(
                    clock_in_id=first_in.id,
                    clock_out_id=clock_out.id,
                    work_date=date(2026, 8, 27),
                ),
                WorkSession(
                    clock_in_id=second_in.id,
                    clock_out_id=clock_out.id,
                    work_date=date(2026, 8, 28),
                ),
            )
        )

        with pytest.raises(IntegrityError, match="work_sessions.clock_out_id"):
            session.commit()


@pytest.mark.parametrize(
    ("clock_in_action", "clock_out_action", "error"),
    [
        (ClockAction.OUT, None, WORK_SESSION_CLOCK_IN_ACTION_ERROR),
        (ClockAction.IN, ClockAction.IN, WORK_SESSION_CLOCK_OUT_ACTION_ERROR),
    ],
    ids=("clock-in-role", "clock-out-role"),
)
def test_insert_requires_events_to_match_their_session_roles(
    work_session_engine: Engine,
    clock_in_action: ClockAction,
    clock_out_action: ClockAction | None,
    error: str,
) -> None:
    with get_session(work_session_engine) as session:
        clock_in = punch(clock_in_action, hour=9)
        clock_out = (
            punch(clock_out_action, hour=10) if clock_out_action is not None else None
        )
        session.add_all(event for event in (clock_in, clock_out) if event is not None)
        session.flush()
        session.add(
            WorkSession(
                clock_in_id=clock_in.id,
                clock_out_id=clock_out.id if clock_out is not None else None,
                work_date=date(2026, 8, 27),
            )
        )

        with pytest.raises(IntegrityError, match=re.escape(error)):
            session.commit()


@pytest.mark.parametrize(
    ("role", "error"),
    [
        ("clock_in", WORK_SESSION_CLOCK_IN_ACTION_ERROR),
        ("clock_out", WORK_SESSION_CLOCK_OUT_ACTION_ERROR),
    ],
)
def test_update_cannot_reverse_an_existing_event_role(
    work_session_engine: Engine,
    role: Literal["clock_in", "clock_out"],
    error: str,
) -> None:
    with get_session(work_session_engine) as session:
        clock_in = punch(ClockAction.IN, hour=9)
        clock_out = punch(ClockAction.OUT, hour=10)
        replacement = punch(
            ClockAction.OUT if role == "clock_in" else ClockAction.IN,
            hour=11,
        )
        session.add_all((clock_in, clock_out, replacement))
        session.flush()
        work_session = WorkSession(
            clock_in_id=clock_in.id,
            clock_out_id=clock_out.id,
            work_date=date(2026, 8, 27),
        )
        session.add(work_session)
        session.commit()

        if role == "clock_in":
            work_session.clock_in_id = replacement.id
        else:
            work_session.clock_out_id = replacement.id
        with pytest.raises(IntegrityError, match=re.escape(error)):
            session.commit()


def test_valid_non_role_updates_remain_available(work_session_engine: Engine) -> None:
    with get_session(work_session_engine) as session:
        clock_in = punch(ClockAction.IN, hour=9)
        session.add(clock_in)
        session.flush()
        work_session = WorkSession(
            clock_in_id=clock_in.id,
            work_date=date(2026, 8, 27),
        )
        session.add(work_session)
        session.commit()

        work_session.note = "Customer workshop"
        session.commit()

        assert work_session.note == "Customer workshop"


def test_public_trigger_callbacks_are_reversible(work_session_engine: Engine) -> None:
    clock_event_table = cast("Table", ClockEvent.__table__)
    work_session_table = cast("Table", WorkSession.__table__)
    with work_session_engine.begin() as connection:
        drop_clock_event_update_trigger(clock_event_table, connection)
        drop_work_session_action_triggers(work_session_table, connection)
    assert stored_trigger(work_session_engine, CLOCK_EVENT_UPDATE_TRIGGER) is None
    assert (
        stored_trigger(work_session_engine, WORK_SESSION_ACTION_INSERT_TRIGGER) is None
    )
    assert (
        stored_trigger(work_session_engine, WORK_SESSION_ACTION_UPDATE_TRIGGER) is None
    )

    with work_session_engine.begin() as connection:
        create_clock_event_update_trigger(clock_event_table, connection)
        create_work_session_action_triggers(work_session_table, connection)
    assert stored_trigger(work_session_engine, CLOCK_EVENT_UPDATE_TRIGGER)
    assert stored_trigger(work_session_engine, WORK_SESSION_ACTION_INSERT_TRIGGER)
    assert stored_trigger(work_session_engine, WORK_SESSION_ACTION_UPDATE_TRIGGER)


def test_trigger_callbacks_ignore_non_sqlite_dialects() -> None:
    candidate = Mock()
    candidate.dialect.name = "postgresql"
    connection = cast("Connection", candidate)
    clock_event_table = cast("Table", ClockEvent.__table__)
    work_session_table = cast("Table", WorkSession.__table__)

    create_clock_event_update_trigger(clock_event_table, connection)
    drop_clock_event_update_trigger(clock_event_table, connection)
    create_work_session_action_triggers(work_session_table, connection)
    drop_work_session_action_triggers(work_session_table, connection)

    candidate.exec_driver_sql.assert_not_called()


def test_registration_and_operation_boundaries_reject_invalid_values() -> None:
    invalid_table = cast("FromClause", object())
    with pytest.raises(TypeError, match="must be registered on a Table"):
        register_clock_event_immutability(invalid_table)
    with pytest.raises(TypeError, match="must be registered on a Table"):
        register_work_session_action_invariants(invalid_table)

    unsupported = cast("TriggerOperation", "DELETE")
    with pytest.raises(AssertionError, match="DELETE"):
        work_session_action_trigger_name(unsupported)
