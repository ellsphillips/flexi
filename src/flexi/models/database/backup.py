"""Taking a copy of the database that is safe to rely on.

``shutil.copy2`` copies a live file. If the application is open in another
terminal mid-write, the copy can be torn -- and a torn copy is worse than no
copy, because it is the artefact somebody is told they can fall back on.
``sqlite3.Connection.backup`` takes a consistent snapshot through the database
engine instead, and it works while the source is in use.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from flexi.locations import backups_directory, ensure

PROTECTED_PREFIX = "pre-init_"
"""A snapshot taken before a reset. Never aged out by the migration pruner."""


def snapshot(source: Path, *, prefix: str = PROTECTED_PREFIX) -> Path:
    """A consistent copy of the database, in the backups directory.

    Suffixed rather than overwritten. The migration backups use one-second
    granularity, and two things happening in the same second is exactly what a
    reset does.
    """
    directory = ensure(backups_directory())
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"{prefix}{source.stem}_{stamp}.bak"

    attempt = 2
    while target.exists():
        target = directory / f"{prefix}{source.stem}_{stamp}_{attempt}.bak"
        attempt += 1

    with sqlite3.connect(source) as origin, sqlite3.connect(target) as copy:
        origin.backup(copy)
    return target


def verify(backup: Path) -> bool:
    """The copy opens, passes an integrity check, and carries a stamp."""
    try:
        with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as connection:
            ok = connection.execute("PRAGMA integrity_check").fetchone()
            if not ok or ok[0] != "ok":
                return False
            stamped = connection.execute(
                "SELECT 1 FROM alembic_version LIMIT 1"
            ).fetchone()
    except sqlite3.DatabaseError:
        return False
    return stamped is not None
