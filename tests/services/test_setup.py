"""What "am I set up" answers when the database will not say.

Three doubts reach :func:`flexi.services.setup.is_initialised` and they do not
resolve the same way. Before the migration stamp, a doubt means "not a Flexi",
because a zero-byte file left by a crashed invocation stats exactly like an
install. After it, the fail-safe inverts: a stamped database that will not
answer a settings query belongs to somebody with a year of records, and telling
them to run ``flexi init`` is the worst advice available.

`tests/test_init_guard.py` covers the answers a normal install gives. These are
the ones a damaged one gives, which is where the inversion is either right or
silently backwards.
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
"""The two tests below need a file the current user genuinely cannot open.

`Path.chmod` on Windows is not that. It maps the whole mode onto the read-only
attribute, so `chmod(0o000)` leaves a file every process can still read, and a
test asserting otherwise would pass by describing something that had not
happened. Denying a read there means an ACL, which is a great deal of machinery
to reach a branch the other two platforms reach in a line.

What is skipped is the *arrangement*, not the behaviour: `stamped_and_configured`
has no platform in it, and a connection that raises is a connection that raises.
"""


@pytest.fixture
def unreadable(tmp_path: Path) -> Iterator[Path]:
    """A database file the current user is not allowed to open.

    Permissions are put back afterwards: a file nobody can read is a file the
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
def test_a_database_that_cannot_be_opened_is_not_an_install(unreadable: Path) -> None:
    """Refusing to answer must not become a traceback before the first screen.

    ``is_initialised`` is the first thing every command runs, and the file
    existing is not the same as the file being readable — a database on a
    detached network share, or one written by another user, reaches the
    connection and raises. Answering "not set up" offers ``flexi init``;
    raising prints a stack trace over the whole CLI.
    """
    assert setup.is_initialised(unreadable) is False


@needs_permissions
def test_an_unopenable_database_is_never_remembered_as_ready(
    unreadable: Path,
) -> None:
    """Permission can be granted a second later, and nothing invalidates the memo."""
    setup.is_initialised(unreadable)
    assert unreadable not in setup._INITIALISED


def test_a_stamp_table_with_no_stamp_in_it_is_not_an_install(tmp_path: Path) -> None:
    """A migration interrupted between creating the table and writing the row.

    The table exists, so the query succeeds and returns nothing at all. Reading
    "no error" as "stamped" would send a half-migrated database on to the
    settings check, where a missing settings row is generously forgiven.
    """
    db = tmp_path / "db.db"
    build(db, "CREATE TABLE alembic_version (version_num varchar(32))")

    assert setup.is_initialised(db) is False


def test_a_stamped_database_with_no_settings_table_is_left_alone(
    tmp_path: Path,
) -> None:
    """Past the stamp, an unanswerable question is not the user's problem.

    Schema drift on a real database — a settings table renamed by a migration
    this build has not heard of — has to read as "set up", because the
    alternative is inviting somebody with a year of records to re-run setup.
    """
    db = tmp_path / "db.db"
    build(db, *STAMP)

    assert setup.is_initialised(db) is True


def test_a_stamped_database_without_settings_is_remembered(tmp_path: Path) -> None:
    """The affirmative is memoised, so the second command does not reconnect."""
    db = tmp_path / "db.db"
    build(db, *STAMP)
    setup.is_initialised(db)

    assert db in setup._INITIALISED
    setup.forget(db)
    assert db not in setup._INITIALISED
