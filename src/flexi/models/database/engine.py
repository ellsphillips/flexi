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

    Public, and named for what it does rather than for how: `migrations/env.py`
    carried a byte-identical private copy, so the guarantee held on whichever
    of the two connections the reader happened to look at.
    """
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
    event.listen(engine, "connect", enforce_foreign_keys)
    return engine


def get_session(engine: Engine) -> Session:
    """A session on an engine somebody else owns and will dispose of.

    The engine is required. Defaulted, it built one nobody held a reference to
    and nobody disposed of -- and on Windows an undisposed engine keeps the
    SQLite file open, which is what stops `flexi init` deleting it.
    """
    return Session(engine)


@contextmanager
def database_scope(db_path: Path | None = None) -> Iterator[tuple[Engine, Session]]:
    """Own one engine and session for exactly as long as a caller needs them.

    Each cleanup is registered immediately after its resource is acquired. If
    session construction fails, the engine is therefore still disposed; if
    anything later in the caller fails, the session is closed before its engine.
    An :class:`~contextlib.ExitStack` also lets a longer-lived owner transfer or
    embed this scope without duplicating the cleanup operations.
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
