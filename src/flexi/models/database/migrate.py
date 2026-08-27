"""Bringing the database up to head, and getting out of the way when it is.

Alembic costs a hundred and thirty milliseconds to import and knows how to
answer one question: what does this database still need? On a database that
needs nothing -- every run but the one after an upgrade -- that is the whole
cost of the command. So the question is asked twice: once cheaply, against
:data:`HEAD`, with nothing but the SQLAlchemy already loaded; and only if that
says there is work to do, expensively, by Alembic itself.

:data:`HEAD` is the duplicate that buys it, and `tests/test_migrations.py` is
what stops it drifting from the real head.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

import flexi
from flexi.locations import backups_directory, database_file, ensure
from flexi.models.database.backup import (
    PROTECTED_PREFIX,
    ROUTINE_PREFIX,
    snapshot,
)
from flexi.models.database.engine import create_db_engine

__all__ = (
    "HEAD",
    "MAX_BACKUPS",
    "MigrationConfig",
    "alembic_config",
    "backup_database",
    "current_revision",
    "prune_backups",
    "run_migrations",
)


if TYPE_CHECKING:
    from alembic.config import Config as MigrationConfig
else:

    class MigrationConfig(Protocol):
        """Runtime view of the config yielded by :func:`alembic_config`.

        Type checkers see the concrete :class:`alembic.config.Config`; runtime
        introspection sees this equivalent public surface without paying to
        import Alembic on the already-at-head path.
        """

        @property
        def attributes(self) -> MutableMapping[str, object]:
            """Objects passed directly to Alembic's migration environment."""
            ...

        def set_main_option(self, name: str, value: str) -> None:
            """Set one string-valued Alembic option."""
            ...


MAX_BACKUPS = 10

HEAD = "0011"
"""The revision a fully migrated database is stamped with.

Written down so the common case -- already at head -- can be settled without
importing Alembic to ask. Kept honest by a test that reads the real head off
the script directory and compares.
"""

_LOGGER = logging.getLogger(__name__)


@contextmanager
def alembic_config(db_path: Path) -> Iterator[MigrationConfig]:
    """An Alembic config wired to an engine on ``db_path``, disposed on exit.

    The engine is handed over rather than a URL, because a config value is not
    a place to keep a path. Alembic's options go through ConfigParser, which
    reads ``%`` as the start of an interpolation: somebody under
    ``C:/Users/100%pure`` got ``ValueError: invalid interpolation syntax``
    instead of an application, and every migration on that machine failed. The
    escaping still has to be done for ``script_location``, which has nowhere
    else to live -- Flexi's own install path can contain one too.

    Disposed rather than left to the collector, because an undisposed engine
    leaves the SQLite file open, and Windows will not delete a file that is.
    """
    from alembic.config import Config

    engine = create_db_engine(db_path)
    cfg = Config()
    migrations_dir = Path(flexi.__file__).resolve().parent / "migrations"
    cfg.set_main_option("script_location", str(migrations_dir).replace("%", "%%"))
    cfg.attributes["engine"] = engine
    try:
        yield cfg
    finally:
        engine.dispose()


def current_revision(db_path: Path) -> str | None:
    """The revision the database is stamped with, or ``None`` for an empty one.

    Read straight out of ``alembic_version`` rather than through
    ``MigrationContext``, which is the same single-column query with Alembic's
    import in front of it. A database with no such table has never been
    migrated, which is the ``None`` this answers.
    """
    engine = create_db_engine(db_path)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("select version_num from alembic_version")
            ).first()
    except DatabaseError:
        return None
    else:
        return None if row is None else str(row[0])
    finally:
        engine.dispose()


def backup_database(db_path: Path | None = None) -> Path | None:
    """A snapshot taken before a migration, or ``None`` if there is nothing yet.

    Through `backup.snapshot`, which is the module that knows how to copy a
    database that might be open. This used `shutil.copy2` -- the exact call
    `backup.py`'s docstring names as the thing it exists to avoid -- so the copy
    taken immediately before a schema change was the one copy in the
    application that could be torn.

    An empty prefix, so these age out under `_cleanup_old_backups`. The
    prefixed ones are the reset snapshots, which never do.
    """
    if db_path is None:
        db_path = database_file()
    if not db_path.exists():
        return None
    return snapshot(db_path, prefix=ROUTINE_PREFIX)


def prune_backups(directory: Path) -> None:
    """Keep only the latest MAX_BACKUPS files, and every protected one.

    Housekeeping runs after a backup has already been taken, so a full disk or
    a read-only directory here must not fail the migration that motivated it.

    Snapshots taken before a reset are never pruned. They are the only copy of
    records somebody chose to erase, `flexi init` calls them the one way back,
    and ten routine migration backups would otherwise age one out inside a
    fortnight of ordinary upgrades.
    """
    try:
        backups = sorted(
            (
                path
                for path in directory.glob("*.bak")
                if not path.name.startswith(PROTECTED_PREFIX)
            ),
            key=lambda p: p.stat().st_mtime,
        )
        for old in backups[:-MAX_BACKUPS]:
            old.unlink()
    except OSError:
        _LOGGER.warning("could not prune old backups", exc_info=True)


def run_migrations(db_path: Path | None = None) -> None:
    """Backup the database (if needed) and apply all pending migrations.

    The already-at-head case returns before Alembic is imported at all. It used
    to build a config, parse every script in the versions directory to work out
    the head, and open a second connection to read the stamp -- a hundred and
    forty milliseconds, on every command, to conclude there was nothing to do.
    """
    if db_path is None:
        db_path = database_file()

    ensure(db_path.parent)

    if db_path.exists():
        if current_revision(db_path) == HEAD:
            return

        backup = backup_database(db_path)
        if backup is None:
            msg = "Database file exists but backup failed"
            raise RuntimeError(msg)

        prune_backups(backups_directory())

    from alembic import command

    with alembic_config(db_path) as cfg:
        command.upgrade(cfg, "head")
