"""Migrations, and the backups taken before them.

A migration that half-applies and takes the backup with it is the one failure
this database cannot recover from, so each step is checked for what it leaves
behind when it fails as well as when it succeeds.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import Engine, text

from flexi.locations import backups_directory, database_file
from flexi.models.database.backup import read_only, snapshot, verify
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.migrate import (
    DatabaseRevision,
    RevisionState,
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


# ---------- the default database ----------


class TestTheDefaultDatabase:
    """The argumentless calls resolve to the real database.

    Almost every caller passes a path, so the defaults are reached only by the
    short-lived CLI commands, where a wrong default would raise nothing.
    """

    def test_a_session_with_no_engine_reaches_the_real_db(self) -> None:
        engine = create_db_engine()
        try:
            with get_session(engine) as session:
                bound = session.get_bind()
        finally:
            engine.dispose()
        assert isinstance(bound, Engine)
        assert bound.url.database == str(database_file())

    def test_migrations_with_no_path_stamp_the_real_db(self) -> None:
        """A wrong default would migrate an unread file and raise nothing."""
        run_migrations()

        assert verify(database_file()), "the real database was not migrated"

    def test_a_backup_with_no_path_copies_the_real_db(self) -> None:
        """`sqlite3.Connection.backup` writes a fresh file, not a byte copy."""
        live = database_file()
        run_migrations(live)

        backup = backup_database()

        assert backup is not None
        assert backup.parent == backups_directory()
        assert verify(backup), "the copy is not one somebody could fall back on"


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


class Halting(sqlite3.Connection):
    """A connection whose copy stops partway through, as a full disk does."""

    def backup(self, *args: Any, **kwargs: Any) -> None:
        msg = "disk I/O error"
        raise sqlite3.OperationalError(msg)


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
            "flexi.models.database.backup.backups_directory", lambda: backup_dir
        )
        backup = backup_database(db_path)
        assert backup is not None
        assert backup.exists()
        assert backup.parent == backup_dir
        assert backup.suffix == ".bak"
        assert backup.stat().st_size == db_path.stat().st_size

    def test_a_copy_that_fails_partway_does_not_stay(
        self, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise a truncated file is left behind, named like a backup."""
        backup_dir = tmp_path / "backups"
        monkeypatch.setattr(
            "flexi.models.database.backup.backups_directory", lambda: backup_dir
        )
        run_migrations(db_path)
        real = sqlite3.connect
        monkeypatch.setattr(
            sqlite3,
            "connect",
            lambda database, **kwargs: real(database, factory=Halting, **kwargs),
        )

        with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
            snapshot(db_path)

        assert list(backup_dir.glob("*.bak")) == []

    def test_no_backup_on_fresh_db(
        self, db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        monkeypatch.setattr(
            "flexi.models.database.backup.backups_directory", lambda: backup_dir
        )
        run_migrations(db_path)
        backups = list(backup_dir.glob("*.bak"))
        assert backups == []


class TestOpeningWithoutWriting:
    """The connection used for every read of a database Flexi does not own."""

    def test_a_missing_database_is_not_opened_into_existence(
        self, db_path: Path
    ) -> None:
        """``sqlite3.connect`` creates the file; asking after one must not."""
        with pytest.raises(FileNotFoundError, match="No database at"):
            read_only(db_path)

        assert not db_path.exists()

    def test_a_missing_copy_does_not_verify(self, db_path: Path) -> None:
        assert verify(db_path) is False
        assert not db_path.exists()

    def test_nothing_can_be_written_through_it(self, db_path: Path) -> None:
        run_migrations(db_path)

        with (
            closing(read_only(db_path)) as connection,
            pytest.raises(sqlite3.OperationalError, match="readonly"),
        ):
            connection.execute("DELETE FROM alembic_version")

    def test_the_database_reaches_sqlite_as_a_path(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`Path.as_uri` gives a UNC path an authority SQLite rejects."""
        run_migrations(db_path)
        seen: list[tuple[Any, dict[str, Any]]] = []
        real = sqlite3.connect

        def spy(database: Any, **kwargs: Any) -> sqlite3.Connection:
            seen.append((database, kwargs))
            connection: sqlite3.Connection = real(database, **kwargs)
            return connection

        monkeypatch.setattr(sqlite3, "connect", spy)
        with closing(read_only(db_path)):
            pass

        assert [database for database, _ in seen] == [db_path]


# ---------- backup failure ----------


class TestBackupFailure:
    def test_run_migrations_raises_when_backup_fails(self, db_path: Path) -> None:
        # Create a DB so it exists
        run_migrations(db_path)
        # Patch backup to return None (simulate failure)
        with (
            patch("flexi.models.database.migrate.backup_database", return_value=None),
            # Force current != head so the backup path is taken. A real
            # revision: a stamp this build cannot place is refused earlier.
            patch(
                "flexi.models.database.migrate.current_revision",
                return_value=DatabaseRevision(RevisionState.STAMPED, "0014"),
            ),
            pytest.raises(RuntimeError, match="backup failed"),
        ):
            run_migrations(db_path)


# ---------- verifying a copy ----------

STAMP = b"2026-03-01"
"""A booked date unique to one row: once in the table pages, once in the index."""

REWRITTEN = b"1999-01-01"
"""What that date becomes in the table alone, at the same length so nothing moves."""


def populated(path: Path) -> Path:
    """Return a migrated database with enough booked days to fill several pages."""
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
    """Return how many rows a full table scan finds under that date.

    ``NOT INDEXED`` because SQLite would otherwise answer a lookup on ``date``
    out of the index and never read the row.
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

    Most matches in the file are cells a rebalanced b-tree left in unallocated
    space, which only a SQLite built with ``SQLITE_SECURE_DELETE`` clears, so
    each match is rewritten in turn and the file kept at the one a table scan
    can see. The index still carries the old key: a copy taken mid-write.
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
    """A backup that cannot be restored has to be refused.

    A copy taken mid-write opens perfectly and is still wrong, and it is the
    artefact a person is told to fall back on.
    """

    def test_an_intact_copy_is_accepted(self, db_path: Path) -> None:
        """The control: without it, the refusals below prove nothing."""
        assert verify(populated(db_path))

    def test_a_copy_that_disagrees_with_its_index_is_refused(
        self, db_path: Path
    ) -> None:
        """Nothing short of an integrity check notices a torn copy."""
        tear(populated(db_path))

        assert not verify(db_path)

    def test_a_copy_that_is_not_a_database_is_refused(self, db_path: Path) -> None:
        """A backup interrupted before it wrote a header is a file, not a copy."""
        db_path.write_bytes(b"this is not a database")
        assert not verify(db_path)

    def test_a_copy_carrying_no_schema_version_is_refused(self, db_path: Path) -> None:
        """An unstamped database cannot be migrated onto the current schema."""
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
        """Alembic creates ``alembic_version`` before it writes the revision."""
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
        with (
            patch(
                "alembic.command.upgrade",
                side_effect=RuntimeError("migration exploded"),
            ),
            pytest.raises(RuntimeError, match="migration exploded"),
        ):
            run_migrations(db_path)
