from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.orm import Session

from flexi.locations import database_file


def _set_sqlite_pragma(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    """Enable SQLite foreign key enforcement on every connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_db_engine(db_path: Path | None = None) -> Engine:
    """Create a SQLAlchemy engine with foreign-key enforcement.

    The URL is built rather than formatted. ``f"sqlite:///{db_path}"`` is a
    string that then gets parsed as a URL, and a path is not a URL: a ``?``
    anywhere in it opens the query string, so `~/a?b/db.db` was read as a
    database called `~/a` and the application failed to open a file that was
    sitting there. :meth:`URL.create` takes the path as the value it is.
    """
    if db_path is None:
        db_path = database_file()
    engine = create_engine(URL.create("sqlite", database=str(db_path)))
    event.listen(engine, "connect", _set_sqlite_pragma)
    return engine


def get_session(engine: Engine | None = None) -> Session:
    """Create a new database session."""
    if engine is None:
        engine = create_db_engine()
    return Session(engine)
