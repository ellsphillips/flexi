"""Migrations, and the backups taken before them.

A migration that half-applies and takes the backup with it is the one failure
this database cannot recover from, so each step is checked for what it leaves
behind when it fails as well as when it succeeds.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import Engine, text

from flexi.locations import backups_directory, database_file
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.backup import verify
from flexi.models.database.migrate import backup_database, run_migrations


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


# ---------- foreign-key enforcement ----------


class TestForeignKeyEnforcement:
    def test_pragma_is_enabled(self, engine: Engine) -> None:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA foreign_keys")).scalar()
            assert result == 1


# ---------- the default database ----------


class TestTheDefaultDatabase:
    """What the argumentless calls resolve to.

    Almost every caller passes an explicit path, so the defaults are exercised
    only by the short-lived CLI commands -- and a default pointing somewhere
    else would not raise. It would give `flexi status` a private empty database
    and a cheerful "not clocked in" for somebody who is.
    """

    def test_a_session_opened_with_no_engine_reaches_the_real_database(self) -> None:
        bound = None
        with get_session() as session:
            bound = session.get_bind()
        assert isinstance(bound, Engine)
        assert bound.url.database == str(database_file())

    def test_migrations_asked_for_no_path_stamp_the_real_database(self) -> None:
        """Startup passes no path at all, and every command afterwards does.

        A default resolving anywhere else would migrate a file nobody reads and
        raise nothing, leaving the database the application then opens
        unstamped -- which does not look like a fault either. It looks like an
        empty timesheet.
        """
        run_migrations()

        assert verify(database_file()), "the real database was not migrated"

    def test_a_backup_asked_for_no_path_copies_the_real_database(self) -> None:
        """Byte for byte, so nothing but the live database can have produced it."""
        live = database_file()
        run_migrations(live)

        backup = backup_database()

        assert backup is not None
        assert backup.parent == backups_directory()
        assert backup.read_bytes() == live.read_bytes()


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


# ---------- verifying a copy ----------

STAMP = b"2026-03-01"
"""A booked date unique to one row, so it appears once in the table pages and
once in the index built over them."""

REWRITTEN = b"1999-01-01"
"""What that date becomes in the table alone. The same length, so the rewrite
stays inside the cell it lands in and moves nothing else on the page."""


def populated(path: Path) -> Path:
    """A migrated database with enough booked days to fill several pages."""
    run_migrations(path)
    first = date(2026, 1, 1)
    connection = sqlite3.connect(path)
    try:
        connection.executemany(
            "INSERT INTO absence_days (date, absence_type, portion)"
            " VALUES (?, 'ANNUAL', 'FULL')",
            [((first + timedelta(days=n)).isoformat(),) for n in range(400)],
        )
        connection.commit()
    finally:
        connection.close()
    return path


def scan_finds(path: Path, booked: bytes) -> int:
    """How many rows a full table scan reads under that date.

    ``NOT INDEXED`` because the index is the thing not to be trusted here: left
    to itself SQLite answers a lookup on ``date`` out of the index and never
    goes near the row.
    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        found = connection.execute(
            "SELECT count(*) FROM absence_days NOT INDEXED WHERE date = ?",
            (booked.decode(),),
        ).fetchone()
    finally:
        connection.close()
    return int(found[0])


def tear(path: Path) -> Path:
    """Rewrite one date in the table pages, leaving the index over it alone.

    Which of the file's copies of that date to rewrite cannot be assumed. A
    b-tree that has rebalanced leaves the cells it moved behind in the
    unallocated part of a page, so most matches in the file are dead space that
    nothing ever reads, and only a SQLite compiled with ``SQLITE_SECURE_DELETE``
    clears it. Whether an interpreter's SQLite was is not a question of version
    and not something this project picks -- CPython 3.13.11 ships one that
    clears and 3.13.15 one that does not -- so rewriting the first match tore
    the file on one machine and left it pristine on the next.

    Each match is therefore rewritten in turn and the file kept at the one a
    table scan can see, which is what it means for a byte to have been holding
    a row. The index still carries the old key, so what is left is a copy taken
    mid-write: some pages from before it, some from after.
    """
    original = path.read_bytes()
    offset = original.find(STAMP)
    while offset != -1:
        path.write_bytes(
            original[:offset] + REWRITTEN + original[offset + len(STAMP) :]
        )
        if scan_finds(path, REWRITTEN) == 1:
            return path
        offset = original.find(STAMP, offset + 1)

    path.write_bytes(original)
    msg = f"no copy of {STAMP!r} in the file was one SQLite reads"
    raise AssertionError(msg)


class TestVerifyingACopy:
    """What stands between somebody and a backup that cannot be restored.

    A copy taken while the application was mid-write can open perfectly and
    still be wrong, and it is the one artefact a person is told they can fall
    back on. Every refusal below has to be a refusal, because the alternative is
    finding out at the moment the original is gone.
    """

    def test_an_intact_copy_is_accepted(self, db_path: Path) -> None:
        """The control: without it, the refusals below prove nothing."""
        assert verify(populated(db_path))

    def test_a_copy_that_no_longer_agrees_with_its_own_index_is_refused(
        self, db_path: Path
    ) -> None:
        """The torn copy that opens cleanly.

        One date rewritten in the table and not in the index over it: SQLite
        connects, answers queries and quietly cannot find that booking by date.
        Nothing short of an integrity check notices, which is why `verify` runs
        one rather than settling for the file opening.
        """
        tear(populated(db_path))

        assert not verify(db_path)

    def test_a_copy_that_is_not_a_database_at_all_is_refused(
        self, db_path: Path
    ) -> None:
        """A backup interrupted before it wrote a header is a file, not a copy."""
        db_path.write_bytes(b"this is not a database")
        assert not verify(db_path)

    def test_a_copy_carrying_no_schema_version_is_refused(self, db_path: Path) -> None:
        """An unstamped database cannot be migrated onto the current schema.

        Restoring one would give Alembic a file it has to guess the shape of,
        and guessing wrong is what a backup exists to avoid.
        """
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("CREATE TABLE clock_events (id integer primary key)")
            connection.commit()
        finally:
            connection.close()

        assert not verify(db_path)

    def test_a_copy_whose_version_table_is_empty_is_refused(
        self, db_path: Path
    ) -> None:
        """The unstamped database that does not announce itself.

        Alembic creates ``alembic_version`` and then writes the revision into
        it, so a copy taken between the two carries the table and no row. That
        one asks for `SELECT 1 FROM alembic_version` and gets nothing back
        rather than an error, so it is the only unstamped file that reaches the
        last line of `verify` at all — every other one has already been turned
        away by the exception.
        """
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("CREATE TABLE alembic_version (version_num varchar)")
            connection.commit()
        finally:
            connection.close()

        assert not verify(db_path)


# ---------- migration failure ----------


class TestMigrationFailure:
    def test_bad_migration_raises(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If alembic upgrade fails, the error propagates."""
        with (
            patch(
                "flexi.models.database.migrate.command.upgrade",
                side_effect=RuntimeError("migration exploded"),
            ),
            pytest.raises(RuntimeError, match="migration exploded"),
        ):
            run_migrations(db_path)
