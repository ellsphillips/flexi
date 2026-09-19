from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session
from sqlalchemy.pool import ConnectionPoolEntry

from flexi.locations import database_file
from flexi.models.database.lease import LeaseMode, database_lease

__all__ = (
    "create_db_engine",
    "database_scope",
    "enforce_foreign_keys",
    "get_session",
)


def enforce_foreign_keys(
    dbapi_connection: DBAPIConnection,
    _connection_record: ConnectionPoolEntry,
) -> None:
    """Enable SQLite foreign key enforcement on every connection.

    Shared with `migrations/env.py`, so both connection paths enforce it.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_db_engine(db_path: Path | None = None) -> Engine:
    """Create a SQLAlchemy engine with foreign-key enforcement.

    The URL is built, not formatted: ``f"sqlite:///{db_path}"`` is parsed as a
    URL, where a ``?`` anywhere in the path opens the query string and the rest
    of the path is lost. :meth:`URL.create` takes the path as a value.
    """
    if db_path is None:
        db_path = database_file()
    engine = create_engine(URL.create("sqlite", database=str(db_path)))
    event.listen(engine, "connect", enforce_foreign_keys)
    return engine


def get_session(engine: Engine) -> Session:
    """A session on an engine the caller owns and will dispose of.

    The engine is required, not defaulted: an undisposed engine keeps the
    SQLite file open on Windows, so the caller has to hold the reference that
    disposes of it.
    """
    return Session(engine)


@contextmanager
def database_scope(db_path: Path | None = None) -> Iterator[tuple[Engine, Session]]:
    """Own one engine and session for exactly as long as a caller needs them.

    Each cleanup is registered as soon as its resource is acquired: a failed
    session construction still disposes the engine, and a later failure closes
    the session before its engine. The :class:`~contextlib.ExitStack` also lets
    a longer-lived owner take the whole scope over.
    """
    with ExitStack() as resources:
        if db_path is None:
            db_path = database_file()
        resources.enter_context(database_lease(db_path, LeaseMode.SHARED))
        engine = create_db_engine(db_path)
        resources.callback(engine.dispose)
        session = get_session(engine)
        resources.callback(session.close)
        yield engine, session
