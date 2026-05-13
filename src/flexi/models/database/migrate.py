from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import flexi
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from flexi.locations import backups_directory, database_file
from flexi.models.database.app import create_db_engine

MAX_BACKUPS = 10


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

    backup_dir = backups_directory()
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{db_path.stem}_{timestamp}.bak"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _cleanup_old_backups() -> None:
    """Keep only the latest MAX_BACKUPS files. Silently ignores errors."""
    try:
        backup_dir = backups_directory()
        backups = sorted(backup_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime)
        for old in backups[:-MAX_BACKUPS]:
            old.unlink()
    except Exception:  # noqa: BLE001
        pass


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
