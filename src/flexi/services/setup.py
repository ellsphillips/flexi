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

REQUIRED_SETTINGS = (
    "leave_year_start",
    "working_days",
    "bank_holiday_division",
    "auto_close_time",
)

_INITIALISED: set[Path] = set()
"""Paths known to be set up. Only the affirmative is remembered: False can
become True at any moment, and nothing should have to invalidate that."""


def is_initialised(db_path: Path | None = None) -> bool:
    """True when this machine has a Flexi database that finished setup.

    Memoised per resolved path rather than globally, because the demo, the test
    suite and a ``--db`` override each point somewhere different inside one
    process.
    """
    path = (db_path or database_file()).expanduser()
    if path in _INITIALISED:
        return True
    if not path.is_file():
        return False

    answer = _stamped_and_configured(path)
    if answer:
        _INITIALISED.add(path)
    return answer


def forget(db_path: Path | None = None) -> None:
    """Drop the remembered answer, for the moment after a reset."""
    path = (db_path or database_file()).expanduser()
    _INITIALISED.discard(path)


def _stamped_and_configured(path: Path) -> bool:
    """The database carries a migration stamp and a complete settings row.

    ``mode=ro`` refuses to create the file, which is the invariant
    :mod:`flexi.locations` exists to protect. It is a URI, so the path has to be
    escaped into one rather than pasted into one: ``?`` opens the query string
    and ``#`` opens a fragment, so a home directory containing either was
    truncated to the part before it, and a fully configured Flexi answered "not
    set up on this machine yet" on every run. ``as_uri`` percent-encodes both,
    and SQLite decodes them back.
    """
    try:
        connection = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return False

    try:
        # No stamp, no Flexi. A zero-byte file left behind by a crashed
        # invocation reaches exactly this line, and must answer False -- it
        # stats like an install and is not one.
        try:
            stamped = connection.execute(
                "SELECT 1 FROM alembic_version LIMIT 1"
            ).fetchone()
        except sqlite3.DatabaseError:
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
