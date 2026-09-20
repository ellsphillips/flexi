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
from pathlib import Path, PurePath
from time import monotonic

from flexi import wallclock
from flexi.locations import backups_directory, ensure

__all__ = ("PROTECTED_PREFIX", "ROUTINE_PREFIX", "read_only", "snapshot", "verify")

PROTECTED_PREFIX = "pre-init_"
"""A snapshot taken before a reset. Never aged out by the migration pruner."""

ROUTINE_PREFIX = ""
"""A snapshot taken before a migration. Aged out once there are `MAX_BACKUPS`.

The empty string, so never pass it to `startswith`: every filename begins with
it, and the protected backups would be selected too."""

_BACKUP_TIMEOUT = 30.0
"""Maximum seconds spent copying or waiting for another SQLite writer."""


def snapshot(source: Path, *, prefix: str = PROTECTED_PREFIX) -> Path:
    """A consistent copy of the database, in the backups directory.

    Reserve the filename exclusively before opening SQLite, so simultaneous
    snapshots cannot overwrite each other. A numeric suffix handles collisions.
    """
    directory = ensure(backups_directory())
    stamp = wallclock.utc_now().strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"{prefix}{source.stem}_{stamp}.bak"

    attempt = 2
    while True:
        try:
            with target.open("xb"):
                pass
        except FileExistsError:
            target = directory / f"{prefix}{source.stem}_{stamp}_{attempt}.bak"
            attempt += 1
        else:
            break

    deadline = monotonic() + _BACKUP_TIMEOUT

    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
        if monotonic() >= deadline:
            message = "Database backup timed out; close other database writers"
            raise sqlite3.OperationalError(message)

    try:
        with (
            closing(read_only(source, timeout=0)) as origin,
            closing(sqlite3.connect(target)) as copy,
        ):
            origin.backup(copy, pages=256, progress=check_deadline, sleep=0.05)
    except BaseException:
        # Outside the handles: Windows will not unlink a file it still holds
        # open, and a truncated file named like a backup would be left behind.
        target.unlink(missing_ok=True)
        raise
    return target


def _read_only_uri(database: PurePath) -> str:
    """Encode an absolute filename with an empty SQLite URI authority.

    ``as_uri`` escapes literal percent signs, query markers and fragments. For
    UNC paths it puts the server in the authority, which SQLite rejects; move
    that server into the path, preserving the leading double slash.
    """
    uri = database.as_uri()
    location = uri.removeprefix("file://")
    if not location.startswith("/"):
        uri = f"file:////{location}"
    return f"{uri}?mode=ro"


def read_only(database: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """A connection to an existing database opened read-only by SQLite.

    ``mode=ro`` prevents creation even if the source disappears between the
    existence check and connection. Unlike ``PRAGMA query_only``, it also
    prevents SQLite from recovering a hot journal by writing to the source.

    ``timeout`` is how long a query waits on a database another process is
    writing to, and defaults to SQLite's own five seconds. A reader with a user
    waiting on it wants less.
    """
    if not database.is_file():
        msg = f"No database at {database}"
        raise FileNotFoundError(msg)
    return sqlite3.connect(
        _read_only_uri(database.absolute()), uri=True, timeout=timeout
    )


def verify(backup: Path) -> bool:
    """The copy opens, passes an integrity check, and carries a stamp."""
    try:
        with closing(read_only(backup)) as connection:
            ok = connection.execute("PRAGMA integrity_check").fetchone()
            if not ok or ok[0] != "ok":
                return False
            revisions = connection.execute(
                "SELECT version_num FROM alembic_version LIMIT 2"
            ).fetchall()
    except (OSError, sqlite3.DatabaseError):
        return False
    return (
        len(revisions) == 1
        and isinstance(revisions[0][0], str)
        and bool(revisions[0][0].strip())
    )
