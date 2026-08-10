from __future__ import annotations

import logging
import shutil
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


def _get_alembic_config(db_path: Path) -> Config:
    """Create an Alembic config pointing at our migrations directory."""
    cfg = Config()
    migrations_dir = Path(flexi.__file__).resolve().parent / "migrations"
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


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


def _cleanup_old_backups() -> None:
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

    cfg = _get_alembic_config(db_path)
    script_dir = ScriptDirectory.from_config(cfg)
    head = script_dir.get_current_head()

    if db_path.exists():
        engine = create_db_engine(db_path)
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current = context.get_current_revision()
        engine.dispose()

        if current == head:
            return  # Already up to date

        backup = backup_database(db_path)
        if backup is None:
            msg = "Database file exists but backup failed"
            raise RuntimeError(msg)

        _cleanup_old_backups()

    command.upgrade(cfg, "head")
