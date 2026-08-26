from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

import flexi
from flexi.locations import backups_directory, database_file, ensure
from flexi.models.database.app import create_db_engine

MAX_BACKUPS = 10

log = logging.getLogger(__name__)


@contextmanager
def alembic_config(db_path: Path) -> Iterator[Config]:
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
    """The revision the database is stamped with, or ``None`` for an empty one."""
    engine = create_db_engine(db_path)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def backup_database(db_path: Path | None = None) -> Path | None:
    """Create a timestamped backup under XDG data flexi/backups/.

    Returns the backup path, or None if the database does not exist.
    """
    if db_path is None:
        db_path = database_file()
    if not db_path.exists():
        return None

    backup_dir = ensure(backups_directory())
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{db_path.stem}_{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def prune_backups() -> None:
    """Keep only the latest MAX_BACKUPS files, and every protected one.

    Housekeeping runs after a backup has already been taken, so a full disk or
    a read-only directory here must not fail the migration that motivated it.

    Snapshots taken before a reset are never pruned. They are the only copy of
    records somebody chose to erase, `flexi init` calls them the one way back,
    and ten routine migration backups would otherwise age one out inside a
    fortnight of ordinary upgrades.
    """
    from flexi.models.database.backup import PROTECTED_PREFIX

    try:
        backup_dir = backups_directory()
        backups = sorted(
            (
                path
                for path in backup_dir.glob("*.bak")
                if not path.name.startswith(PROTECTED_PREFIX)
            ),
            key=lambda p: p.stat().st_mtime,
        )
        for old in backups[:-MAX_BACKUPS]:
            old.unlink()
    except OSError:
        log.warning("could not prune old backups", exc_info=True)


def run_migrations(db_path: Path | None = None) -> None:
    """Backup the database (if needed) and apply all pending migrations."""
    if db_path is None:
        db_path = database_file()

    db_path.parent.mkdir(parents=True, exist_ok=True)

    with alembic_config(db_path) as cfg:
        script_dir = ScriptDirectory.from_config(cfg)
        head = script_dir.get_current_head()

        if db_path.exists():
            if current_revision(db_path) == head:
                return  # Already up to date

            backup = backup_database(db_path)
            if backup is None:
                msg = "Database file exists but backup failed"
                raise RuntimeError(msg)

            prune_backups()

        command.upgrade(cfg, "head")
