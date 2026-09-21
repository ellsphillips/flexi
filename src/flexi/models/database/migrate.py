"""Bringing the database up to head, and getting out of the way when it is.

Importing Alembic is most of what a command with nothing to migrate costs, so
the question is asked twice: cheaply first, against :data:`HEAD` with nothing
but the SQLAlchemy already loaded, and then by Alembic itself only when that
says there is work to do.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import text

import flexi
from flexi.locations import backups_directory, database_file, ensure
from flexi.models.database.backup import (
    PROTECTED_PREFIX,
    ROUTINE_PREFIX,
    snapshot,
    verify,
)
from flexi.models.database.engine import create_db_engine
from flexi.models.database.lease import LeaseMode, database_lease

__all__ = (
    "HEAD",
    "MAX_BACKUPS",
    "DatabaseRevision",
    "MigrationConfig",
    "MigrationRefusedError",
    "RevisionState",
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

HEAD = "0016"
"""The revision a fully migrated database is stamped with.

Written down so the already-at-head case settles without importing Alembic.
Enforced by tests/models/test_migrations.py, which reads the real head off the
script directory.
"""

_LOGGER = logging.getLogger(__name__)


class MigrationRefusedError(RuntimeError):
    """A refusal to migrate. The database is exactly as it was.

    Separate from the errors a bug raises, so the command line can print the
    message alone: each of these is a state the user can act on.
    """


class RevisionState(StrEnum):
    """The safely distinguishable states of a database's migration stamp.

    ``ABSENT`` is no file; ``EMPTY`` is a valid SQLite database with no
    application tables, so it is as safe to build from scratch; ``UNSTAMPED``
    is a schema that cannot be tied to a migration, so upgrading it would mean
    guessing what Alembic may overwrite; ``STAMPED`` carries one revision.

    An unreadable, locked, corrupt or structurally ambiguous database is not a
    state: :func:`current_revision` raises instead, because treating it as
    ``UNSTAMPED`` or ``ABSENT`` would send it down the destructive path.
    """

    ABSENT = "absent"
    EMPTY = "empty"
    UNSTAMPED = "unstamped"
    STAMPED = "stamped"


@dataclass(frozen=True, slots=True)
class DatabaseRevision:
    """A database's explicit migration state and, when stamped, its revision."""

    state: RevisionState
    revision: str | None = None

    def __post_init__(self) -> None:
        """Keep the state and its associated revision impossible to contradict."""
        if self.state is RevisionState.STAMPED:
            if not self.revision:
                msg = "A stamped database revision must carry a revision"
                raise ValueError(msg)
        elif self.revision is not None:
            msg = f"A {self.state.value} database cannot carry a revision"
            raise ValueError(msg)


@contextmanager
def alembic_config(db_path: Path) -> Iterator[MigrationConfig]:
    """An Alembic config wired to an engine on ``db_path``, disposed on exit.

    The engine is handed over through ``attributes``, not as a URL: Alembic's
    options go through ConfigParser, which reads ``%`` as the start of an
    interpolation. ``script_location`` has nowhere else to live, so it is
    escaped; an install path can hold a ``%`` too.

    The engine is disposed here because an undisposed one leaves the SQLite
    file open, and Windows will not delete a file that is.
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


def current_revision(db_path: Path) -> DatabaseRevision:
    """Inspect the database's stamp without collapsing unsafe states together.

    Read out of ``sqlite_master`` and ``alembic_version``, not through
    ``MigrationContext``, so the common path never imports Alembic. A missing
    file, a schema-empty database, an unstamped schema and a stamped schema are
    separate results. Database errors propagate: a locked or corrupt file must
    never masquerade as a fresh database.
    """
    if not db_path.exists():
        return DatabaseRevision(RevisionState.ABSENT)

    engine = create_db_engine(db_path)
    try:
        with engine.connect() as connection:
            schema_objects = {
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    text(
                        "SELECT type, name FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%'"
                    )
                )
            }
            if not schema_objects:
                return DatabaseRevision(RevisionState.EMPTY)
            if ("table", "alembic_version") not in schema_objects:
                return DatabaseRevision(RevisionState.UNSTAMPED)

            rows = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).all()
            if not rows:
                return DatabaseRevision(RevisionState.UNSTAMPED)
            if len(rows) != 1:
                msg = "Database carries multiple migration revisions"
                raise MigrationRefusedError(msg)

            revision = rows[0][0]
            if not isinstance(revision, str) or not revision:
                msg = "Database carries an invalid migration revision"
                raise MigrationRefusedError(msg)
            return DatabaseRevision(RevisionState.STAMPED, revision)
    finally:
        engine.dispose()


def backup_database(db_path: Path | None = None) -> Path | None:
    """A snapshot taken before a migration, or ``None`` if there is nothing yet.

    Taken through `backup.snapshot`, which knows how to copy a database that
    may be open. The empty prefix ages these out under :func:`prune_backups`;
    the prefixed reset snapshots never age out.
    """
    if db_path is None:
        db_path = database_file()
    if not db_path.exists():
        return None
    return snapshot(db_path, prefix=ROUTINE_PREFIX)


def prune_backups(directory: Path, keep: Path | None = None) -> None:
    """Keep only the latest MAX_BACKUPS files, and every protected one.

    Housekeeping runs after a backup has already been taken, so a full disk or
    a read-only directory here must not fail the migration that motivated it.

    ``keep`` is that backup, held whatever its age says: filesystem modification
    times on a share or a restored directory can put the copy taken a moment ago
    behind the ten already there. Snapshots taken before a reset are never
    pruned; they are the only copy of the records the reset erased.
    """
    try:
        backups = sorted(
            (
                path
                for path in directory.glob("*.bak")
                if not path.name.startswith(PROTECTED_PREFIX)
            ),
            key=lambda path: (path == keep, path.stat().st_mtime, path.name),
        )
        for old in backups[:-MAX_BACKUPS]:
            old.unlink()
    except OSError:
        _LOGGER.warning("could not prune old backups", exc_info=True)


def _refuse_a_newer_database(cfg: MigrationConfig, stamp: str, db_path: Path) -> None:
    """Stop unless this build knows the revision the database is stamped with.

    A stamp Alembic cannot place belongs to a newer Flexi, which is what a
    downgrade leaves behind. Checked before the recovery copy is taken: Alembic
    fails inside the upgrade instead, and each later command would take another
    copy until every surviving backup is unreadable to this build.
    """
    from alembic.script import ScriptDirectory
    from alembic.util.exc import CommandError

    try:
        ScriptDirectory.from_config(cfg).get_revision(stamp)
    except CommandError as error:
        msg = (
            f"The database at {db_path} was written by a newer Flexi"
            f" (revision {stamp}; this is {flexi.__version__}, which knows up to"
            f" {HEAD}). Upgrade Flexi, or restore a backup from"
            f" {backups_directory()}."
        )
        raise MigrationRefusedError(msg) from error


def run_migrations(db_path: Path | None = None) -> None:
    """Safely apply every pending migration to ``db_path``.

    The already-at-head case returns before Alembic is imported at all.

    Missing and schema-empty databases are fresh and need no recovery copy.
    Existing unstamped schemas are refused, as is a stamp this build has never
    heard of. A stamped database is handed to Alembic only after its snapshot
    passes :func:`flexi.models.database.backup.verify`.
    """
    if db_path is None:
        db_path = database_file()

    ensure(db_path.parent)

    # The cheap shared check lets any number of already-current applications
    # open together. A pending migration then upgrades to exclusive ownership
    # and repeats the check: another starter may have completed it in the gap.
    with database_lease(db_path, LeaseMode.SHARED):
        revision = current_revision(db_path)
        if revision.state is RevisionState.UNSTAMPED:
            msg = "Database has an unstamped schema; migration refused"
            raise MigrationRefusedError(msg)
        if revision.state is RevisionState.STAMPED and revision.revision == HEAD:
            return

    with database_lease(db_path, LeaseMode.EXCLUSIVE):
        revision = current_revision(db_path)
        if revision.state is RevisionState.UNSTAMPED:
            msg = "Database has an unstamped schema; migration refused"
            raise MigrationRefusedError(msg)
        # Only a stamped database carries one: the same question as the state,
        # narrowed to the revision the checks below need.
        stamp = revision.revision
        if stamp == HEAD:
            return

        from alembic import command

        with alembic_config(db_path) as cfg:
            if stamp is not None:
                _refuse_a_newer_database(cfg, stamp, db_path)

                backup = backup_database(db_path)
                if backup is None:
                    msg = "Database file exists but backup failed"
                    raise MigrationRefusedError(msg)
                if not verify(backup):
                    msg = "Database backup did not verify; migration refused"
                    raise MigrationRefusedError(msg)

                prune_backups(backups_directory(), keep=backup)

            command.upgrade(cfg, "head")
