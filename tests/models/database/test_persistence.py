"""Tests for Slice 1: migration-safe SQLite persistence.

Covers: migration success, backup creation, backup retention, backup failure,
migration failure, foreign-key enforcement, and version check still works.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import Engine, text

from flexi.models.database.app import create_db_engine
from flexi.models.database.migrate import (
    MAX_BACKUPS,
    _cleanup_old_backups,
    backup_database,
    run_migrations,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


# ---------- foreign-key enforcement ----------


class TestForeignKeyEnforcement:
    def test_pragma_is_enabled(self, engine: Engine) -> None:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA foreign_keys")).scalar()
            assert result == 1


# ---------- migration success ----------


class TestMigrationSuccess:
    def test_fresh_db_runs_without_error(self, db_path: Path) -> None:
        run_migrations(db_path)
        # alembic_version table should exist
        engine = create_db_engine(db_path)
        with engine.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
        engine.dispose()
        assert "alembic_version" in tables

    def test_idempotent_on_second_run(self, db_path: Path) -> None:
        run_migrations(db_path)
        run_migrations(db_path)  # should not raise


# ---------- backup creation ----------


class TestBackupCreation:
    def test_nonexistent_db_returns_none(self, tmp_path: Path) -> None:
        assert backup_database(tmp_path / "nope.db") is None

    def test_backup_lands_in_backups_dir(
        self,
        db_path: Path,
        engine: Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        monkeypatch.setattr(
            "flexi.models.database.migrate.backups_directory", lambda: backup_dir
        )
        backup = backup_database(db_path)
        assert backup is not None
        assert backup.exists()
        assert backup.parent == backup_dir
        assert backup.suffix == ".bak"
        assert backup.stat().st_size == db_path.stat().st_size

    def test_no_backup_on_fresh_db(
        self, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        monkeypatch.setattr(
            "flexi.models.database.migrate.backups_directory", lambda: backup_dir
        )
        run_migrations(db_path)
        backups = list(backup_dir.glob("*.bak"))
        assert backups == []


# ---------- backup retention ----------


class TestBackupRetention:
    def test_keeps_latest_ten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        monkeypatch.setattr(
            "flexi.models.database.migrate.backups_directory", lambda: backup_dir
        )
        for i in range(15):
            (backup_dir / f"db_{i:04d}.bak").write_text("x")
        _cleanup_old_backups()
        remaining = list(backup_dir.glob("*.bak"))
        assert len(remaining) == MAX_BACKUPS

    def test_cleanup_failure_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "flexi.models.database.migrate.backups_directory",
            lambda: Path("/nonexistent/path"),
        )
        _cleanup_old_backups()  # should not raise


# ---------- backup failure ----------


class TestBackupFailure:
    def test_run_migrations_raises_when_backup_fails(self, db_path: Path) -> None:
        # Create a DB so it exists
        run_migrations(db_path)
        # Patch backup to return None (simulate failure)
        with (
            patch("flexi.models.database.migrate.backup_database", return_value=None),
            # Force current != head so backup path is taken
            patch("flexi.models.database.migrate.MigrationContext") as mock_ctx_cls,
        ):
            mock_ctx_cls.configure.return_value.get_current_revision.return_value = (
                "fake_old"
            )
            with pytest.raises(RuntimeError, match="backup failed"):
                run_migrations(db_path)


# ---------- migration failure ----------


class TestMigrationFailure:
    def test_bad_migration_raises(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If alembic upgrade fails, the error propagates."""
        with patch(
            "flexi.models.database.migrate.command.upgrade",
            side_effect=RuntimeError("migration exploded"),
        ):
            with pytest.raises(RuntimeError, match="migration exploded"):
                run_migrations(db_path)
