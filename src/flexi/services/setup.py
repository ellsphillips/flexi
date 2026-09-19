"""Whether this machine has a Flexi worth opening.

Answered over one read-only sqlite3 connection, with neither SQLAlchemy nor
Alembic imported, so asking costs no migration machinery.

The question is not "does db.db exist": connecting to a missing SQLite path
creates a zero-byte file, so a crashed invocation leaves something that stats
like an install. Nor is it "do the tables exist": every getter in
:mod:`flexi.services.settings` substitutes a default for a missing settings
row, so a migrated but unconfigured database answers confidently and wrongly.
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
"""Paths known to be set up.

Only the affirmative is remembered: False becomes True the moment setup
finishes, and nothing should have to invalidate a cached False.
"""


def is_initialised(db_path: Path | None = None) -> bool:
    """True when this machine has a Flexi database that finished setup.

    Memoised per resolved path, because the demo, the test suite and a ``--db``
    override each point somewhere different inside one process.

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

    Application flows know the one path they reset and call :func:`forget`.
    Test harnesses and embedders switch between several databases in one
    process, and need a supported way to reset every cached answer.
    """
    _INITIALISED.clear()


def stamped_and_configured(path: Path) -> bool:
    """The database carries a migration stamp and a complete settings row.

    Opened by path with ``query_only`` set, not through a ``file:...?mode=ro``
    URI: :meth:`Path.as_uri` renders a Windows UNC path as
    ``file://server/share/...`` and SQLite accepts no authority but an empty
    one, so a data directory on a network share would be refused as an invalid
    URI. The file must already exist, since connecting to a missing path
    creates a zero-byte database. :func:`flexi.models.database.backup.read_only`
    opens a connection the same way; importing it would cost the SQLAlchemy and
    Alembic this module avoids.

    Raises:
        sqlite3.DatabaseError: The file is not a database, or is damaged. That
            is not "no Flexi here", and the advice "run `flexi init`" that
            answer carries would be wrong.
    """
    if not path.is_file():
        return False

    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error:
        return False

    try:
        connection.execute("PRAGMA query_only = 1")

        # No stamp, no Flexi: a zero-byte file reaches this line and answers
        # False. `OperationalError` alone, because "no such table" arrives as
        # one while "file is not a database" and "database disk image is
        # malformed" do not, and those mean the opposite thing.
        try:
            stamped = connection.execute(
                "SELECT 1 FROM alembic_version LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        if stamped is None:
            return False

        # From here the database is real, so the fail-safe inverts: a stamped
        # database whose settings schema has drifted still answers True, rather
        # than sending a user with a year of records to `flexi init`.
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
