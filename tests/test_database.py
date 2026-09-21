"""Schema setup keeps production constraints and SQLite transaction behavior."""

from pathlib import Path

import pytest
from sqlalchemy import Connection, MetaData, event

from flexi.models.database.db import Base
from flexi.models.database.engine import create_db_engine
from tests.database import create_schema


def test_batched_schema_matches_normal_creation(tmp_path: Path) -> None:
    ordinary = create_db_engine(tmp_path / "ordinary.db")
    batched = create_db_engine(tmp_path / "batched.db")
    try:
        Base.metadata.create_all(ordinary)
        create_schema(batched)
        with ordinary.connect() as expected, batched.connect() as actual:
            schema = "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY name"
            assert (
                actual.exec_driver_sql(schema).all()
                == expected.exec_driver_sql(schema).all()
            )
            assert actual.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            for pragma in ("journal_mode", "synchronous"):
                statement = f"PRAGMA {pragma}"
                assert actual.exec_driver_sql(statement).scalar_one() == (
                    expected.exec_driver_sql(statement).scalar_one()
                )
    finally:
        ordinary.dispose()
        batched.dispose()


def test_failed_schema_creation_leaves_no_partial_tables(tmp_path: Path) -> None:
    """A late DDL failure rolls back tables, indexes and after-create triggers."""
    engine = create_db_engine(tmp_path / "failed.db")

    def fail(_metadata: MetaData, _connection: Connection, **_options: object) -> None:
        message = "schema creation failed"
        raise RuntimeError(message)

    event.listen(Base.metadata, "after_create", fail)
    try:
        with pytest.raises(RuntimeError, match="schema creation failed"):
            create_schema(engine)
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT name FROM sqlite_master").all() == []
            )
    finally:
        event.remove(Base.metadata, "after_create", fail)
        engine.dispose()
