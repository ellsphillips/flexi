"""What "am I set up" answers when the database will not say.

Three doubts reach :func:`flexi.services.setup.is_initialised`, and the
fail-safe inverts at the migration stamp. Before it a doubt means "not a
Flexi", because a zero-byte file from a crashed invocation stats like an
install. After it a doubt means "set up", because the database belongs to
someone with a year of records who must not be sent to ``flexi init``.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from flexi.services import setup

STAMP = (
    "CREATE TABLE alembic_version (version_num varchar(32))",
    "INSERT INTO alembic_version VALUES ('0010')",
)


def build(db: Path, *statements: str) -> None:
    """Write a database by hand, so a test can describe a broken one."""
    connection = sqlite3.connect(db)
    try:
        for statement in statements:
            connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


needs_permissions = pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod on Windows sets the read-only bit and cannot deny a read",
)
"""The two tests below need a file the current user cannot open.

`Path.chmod` on Windows maps the whole mode onto the read-only attribute, so
`chmod(0o000)` leaves a file every process can still read. Denying a read there
means an ACL. What is skipped is the arrangement, not the behaviour.
"""


@pytest.fixture
def unreadable(tmp_path: Path) -> Iterator[Path]:
    """A database file the current user is not allowed to open.

    Permissions are put back afterwards: an unreadable file is one the
    temporary directory cleanup cannot always remove either.
    """
    db = tmp_path / "db.db"
    build(db, *STAMP)
    db.chmod(0o000)
    try:
        yield db
    finally:
        db.chmod(0o600)


@needs_permissions
def test_unreadable_database_is_not_an_install(unreadable: Path) -> None:
    """``is_initialised`` is the first thing every command runs.

    A database on a detached share reaches the connection and raises. Answering
    "not set up" offers ``flexi init``; raising prints a stack trace over the CLI.
    """
    assert setup.is_initialised(unreadable) is False


@needs_permissions
def test_unreadable_database_is_not_remembered(unreadable: Path) -> None:
    """Permission can be granted a second later, and nothing clears the memo."""
    setup.is_initialised(unreadable)
    unreadable.chmod(0o600)

    assert setup.is_initialised(unreadable) is True


def test_file_that_is_not_a_database_raises(tmp_path: Path) -> None:
    """A missing table is `OperationalError` and means there is no Flexi here.

    "File is not a database" is the plain `DatabaseError` and means the opposite:
    something is there and cannot be read. False would offer to erase it.
    """
    db = tmp_path / "db.db"
    db.write_bytes(b"\x00 not a database " * 128)

    with pytest.raises(sqlite3.DatabaseError):
        setup.is_initialised(db)


def test_empty_stamp_table_is_not_an_install(tmp_path: Path) -> None:
    """A migration interrupted between creating the table and writing the row.

    The query succeeds and returns nothing. Reading that as "stamped" sends a
    half-migrated database on to the settings check, which forgives a missing row.
    """
    db = tmp_path / "db.db"
    build(db, "CREATE TABLE alembic_version (version_num varchar(32))")

    assert setup.is_initialised(db) is False


def test_missing_settings_table_reads_as_set_up(
    tmp_path: Path,
) -> None:
    """Schema drift past the stamp reads as set up, not as an uninstalled Flexi."""
    db = tmp_path / "db.db"
    build(db, *STAMP)

    assert setup.is_initialised(db) is True


def test_stamped_database_without_settings_is_remembered(tmp_path: Path) -> None:
    """The affirmative is memoised, so the second command does not reconnect."""
    db = tmp_path / "db.db"
    build(db, *STAMP)
    assert setup.is_initialised(db) is True
    db.unlink()

    assert setup.is_initialised(db) is True
    setup.forget(db)
    assert setup.is_initialised(db) is False
