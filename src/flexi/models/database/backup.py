"""Taking a copy of the database that is safe to rely on.

``shutil.copy2`` copies a live file. If the application is open in another
terminal mid-write, the copy can be torn -- and a torn copy is worse than no
copy, because it is the artefact somebody is told they can fall back on.
``sqlite3.Connection.backup`` takes a consistent snapshot through the database
engine instead, and it works while the source is in use.

Every connection here is wrapped in :func:`contextlib.closing`. ``with
sqlite3.connect(...)`` alone is a transaction, not a handle: it commits on the
way out and leaves the connection open. POSIX lets you delete a file somebody
still has open, so nothing ever showed -- and then ``flexi init`` on Windows
took the snapshot, verified it, and raised ``PermissionError: the process
cannot access the file because it is being used by another process`` on the
line that removes the database.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from flexi import wallclock
from flexi.locations import backups_directory, ensure

PROTECTED_PREFIX = "pre-init_"
"""A snapshot taken before a reset. Never aged out by the migration pruner."""

ROUTINE_PREFIX = ""
"""A snapshot taken before a migration. Aged out once there are `MAX_BACKUPS`.

Unprefixed on purpose, and therefore the one prefix that must never be handed
to `startswith`: every filename begins with the empty string, so the test that
looks as though it selects the routine backups would select the protected ones
with them."""


def snapshot(source: Path, *, prefix: str = PROTECTED_PREFIX) -> Path:
    """A consistent copy of the database, in the backups directory.

    Suffixed rather than overwritten. The migration backups use one-second
    granularity, and two things happening in the same second is exactly what a
    reset does.
    """
    directory = ensure(backups_directory())
    stamp = wallclock.utc_now().strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"{prefix}{source.stem}_{stamp}.bak"

    attempt = 2
    while target.exists():
        target = directory / f"{prefix}{source.stem}_{stamp}_{attempt}.bak"
        attempt += 1

    with (
        closing(sqlite3.connect(source)) as origin,
        closing(sqlite3.connect(target)) as copy,
    ):
        origin.backup(copy)
    return target


def verify(backup: Path) -> bool:
    """The copy opens, passes an integrity check, and carries a stamp."""
    try:
        with closing(
            sqlite3.connect(f"{backup.absolute().as_uri()}?mode=ro", uri=True)
        ) as connection:
            ok = connection.execute("PRAGMA integrity_check").fetchone()
            if not ok or ok[0] != "ok":
                return False
            stamped = connection.execute(
                "SELECT 1 FROM alembic_version LIMIT 1"
            ).fetchone()
    except sqlite3.DatabaseError:
        return False
    return stamped is not None
