"""Taking a copy of the database that is safe to rely on.

``shutil.copy2`` copies a live file, so the copy can be torn if the application
is mid-write in another terminal. ``sqlite3.Connection.backup`` takes a
consistent snapshot through the database engine, and works while the source is
in use.

Every connection here is wrapped in :func:`contextlib.closing`. ``with
sqlite3.connect(...)`` alone is a transaction, not a handle: it commits on the
way out and leaves the connection open. Windows refuses to remove a file that
is still open, where POSIX allows it.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from flexi import wallclock
from flexi.locations import backups_directory, ensure

__all__ = ("PROTECTED_PREFIX", "ROUTINE_PREFIX", "read_only", "snapshot", "verify")

PROTECTED_PREFIX = "pre-init_"
"""A snapshot taken before a reset. Never aged out by the migration pruner."""

ROUTINE_PREFIX = ""
"""A snapshot taken before a migration. Aged out once there are `MAX_BACKUPS`.

The empty string, so never pass it to `startswith`: every filename begins with
it, and the protected backups would be selected too."""


def snapshot(source: Path, *, prefix: str = PROTECTED_PREFIX) -> Path:
    """A consistent copy of the database, in the backups directory.

    A numeric suffix is added when the timestamped name already exists; stamps
    have one-second granularity and a reset takes two snapshots in one second.
    """
    directory = ensure(backups_directory())
    stamp = wallclock.utc_now().strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"{prefix}{source.stem}_{stamp}.bak"

    attempt = 2
    while target.exists():
        target = directory / f"{prefix}{source.stem}_{stamp}_{attempt}.bak"
        attempt += 1

    try:
        with (
            closing(sqlite3.connect(source)) as origin,
            closing(sqlite3.connect(target)) as copy,
        ):
            origin.backup(copy)
    except BaseException:
        # Outside the handles: Windows will not unlink a file it still holds
        # open, and a truncated file named like a backup would be left behind.
        target.unlink(missing_ok=True)
        raise
    return target


def read_only(database: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """A connection to an existing database that cannot write to it.

    Opened by path, not through a ``file:...?mode=ro`` URI: :meth:`Path.as_uri`
    renders a Windows UNC path as ``file://server/share/...``, and SQLite
    accepts no authority but an empty one, so a data directory on a network
    share would be refused as an invalid URI.

    The file has to be there: ``sqlite3.connect`` creates an empty database
    where ``mode=ro`` returns an error.

    ``timeout`` is how long a query waits on a database another process is
    writing to, and defaults to SQLite's own five seconds. A reader with a user
    waiting on it wants less.
    """
    if not database.is_file():
        msg = f"No database at {database}"
        raise FileNotFoundError(msg)
    connection = sqlite3.connect(database, timeout=timeout)
    connection.execute("PRAGMA query_only = 1")
    return connection


def verify(backup: Path) -> bool:
    """The copy opens, passes an integrity check, and carries a stamp."""
    try:
        with closing(read_only(backup)) as connection:
            ok = connection.execute("PRAGMA integrity_check").fetchone()
            if not ok or ok[0] != "ok":
                return False
            stamped = connection.execute(
                "SELECT 1 FROM alembic_version LIMIT 1"
            ).fetchone()
    except (OSError, sqlite3.DatabaseError):
        return False
    return stamped is not None
