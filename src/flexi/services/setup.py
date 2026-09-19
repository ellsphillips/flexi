"""Whether this machine has a Flexi worth opening.

Two facts, one read-only connection, and neither SQLAlchemy nor Alembic
imported -- the migration module alone costs about 200ms, and asking "am I set
up" should not pay it.

The check is deliberately not ``db.db exists``. Connecting to a missing SQLite
path creates a zero-byte file, so one crashed invocation leaves something that
stats exactly like an install. Nor is it "the tables exist": every getter in
:mod:`flexi.services.settings` substitutes a default for a missing settings
row, so a migrated-but-unconfigured database answers every question
confidently and wrongly. ``flexi balance show`` on one reports a deficit of
1161 hours computed from a 1 January leave year nobody chose.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flexi.locations import database_file

__all__ = (
    "REQUIRED_SETTINGS",
    "clear_initialisation_cache",
    "forget",
    "is_initialised",
    "stamped_and_configured",
)

REQUIRED_SETTINGS = (
    "leave_year_start",
    "working_days",
    "bank_holiday_division",
    "auto_close_time",
)

_INITIALISED: set[Path] = set()
"""Paths known to be set up. Only the affirmative is remembered: False can
become True at any moment, and nothing should have to invalidate that.

Private because a public handle on it is a way for anything to assert an
install that is not there."""


def is_initialised(db_path: Path | None = None) -> bool:
    """True when this machine has a Flexi database that finished setup.

    Memoised per resolved path rather than globally, because the demo, the test
    suite and a ``--db`` override each point somewhere different inside one
    process.

    Raises:
        sqlite3.DatabaseError: The database is there and cannot be read. See
            :func:`stamped_and_configured`.
    """
    path = (db_path or database_file()).expanduser()
    if path in _INITIALISED:
        return True

    answer = stamped_and_configured(path)
    if answer:
        _INITIALISED.add(path)
    return answer


def forget(db_path: Path | None = None) -> None:
    """Drop the remembered answer, for the moment after a reset."""
    path = (db_path or database_file()).expanduser()
    _INITIALISED.discard(path)


def clear_initialisation_cache() -> None:
    """Forget every remembered database path.

    Normal application flows know the one path they reset and should call
    :func:`forget`.  Test harnesses and embedders can switch between several
    databases in one process, so they need a supported way to reset all cached
    answers without reaching into this module's mutable implementation state.
    """
    _INITIALISED.clear()


def stamped_and_configured(path: Path) -> bool:
    """The database carries a migration stamp and a complete settings row.

    Opened by path with ``query_only`` set, not through a ``file:...?mode=ro``
    URI. :meth:`Path.as_uri` renders a Windows UNC path as
    ``file://server/share/...`` and SQLite accepts no authority but an empty
    one, so a data directory on a network share is refused as an invalid URI
    before it is ever looked for. The file must already exist: connecting to a
    missing path creates a zero-byte database, and not creating one is the
    invariant :mod:`flexi.locations` exists to protect.

    :func:`flexi.models.database.backup.read_only` opens a connection the same
    way. Importing it costs the SQLAlchemy and Alembic this module's docstring
    refuses, so the three lines are said again here instead.

    Raises:
        sqlite3.DatabaseError: The file is not a database, or is damaged. That
            is not "no Flexi here", and the advice that answer carries -- run
            `flexi init` -- is the last thing its owner should act on.
    """
    if not path.is_file():
        return False

    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error:
        return False

    try:
        connection.execute("PRAGMA query_only = 1")

        # No stamp, no Flexi. A zero-byte file left behind by a crashed
        # invocation reaches exactly this line, and must answer False -- it
        # stats like an install and is not one. `OperationalError` alone: "no
        # such table" arrives as one, "file is not a database" and "database
        # disk image is malformed" do not, and they mean the opposite thing.
        try:
            stamped = connection.execute(
                "SELECT 1 FROM alembic_version LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        if stamped is None:
            return False

        # From here the database is real, so the fail-safe inverts. A stamped
        # database whose settings schema has drifted is somebody else's problem
        # to diagnose; telling a user with a year of records to run `flexi init`
        # would be the worst possible advice.
        try:
            columns = ", ".join(REQUIRED_SETTINGS)
            row = connection.execute(
                f"SELECT {columns} FROM settings LIMIT 1"  # noqa: S608 - fixed names
            ).fetchone()
        except sqlite3.DatabaseError:
            return True
    finally:
        connection.close()

    return row is not None and all(field for field in row)
