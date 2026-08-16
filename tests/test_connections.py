"""Every SQLite connection Flexi opens is closed before the call returns.

``with sqlite3.connect(...)`` reads as a handle and is a transaction. It commits
on the way out and leaves the connection open, and POSIX is happy to delete or
replace a file somebody still has open -- so on macOS and Linux the mistake has
no symptom at all.

Windows does not allow it. `flexi init` took its snapshot, verified it, and then
raised ``PermissionError: the process cannot access the file because it is being
used by another process`` on the line that removes the database: the read-only
connection `describe` had opened a moment earlier to count the records was still
there. The reset was unreachable on that platform.

The test is here rather than in the Windows job because a rule only enforced on
one runner is a rule that gets broken on the other two first. It watches the
connections the code actually opens, which is why it says something a mocked
`sqlite3` would not.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from flexi.cli import init as init_cli
from flexi.models.database.backup import snapshot, verify
from flexi.models.database.migrate import run_migrations
from flexi.services import setup

Opened = list[sqlite3.Connection]


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> Iterator[Opened]:
    """Every connection made while the fixture is in force, in order."""
    made: Opened = []
    real = sqlite3.connect

    def spy(database: Any, **kwargs: Any) -> sqlite3.Connection:
        connection: sqlite3.Connection = real(database, **kwargs)
        made.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", spy)
    yield made
    for connection in made:
        connection.close()


def still_open(connections: Opened) -> list[sqlite3.Connection]:
    """The ones that would still be holding the file on Windows.

    A closed connection raises `ProgrammingError` on use, which is the only
    question worth asking it and the one Windows asks with a locked file.
    """
    live = []
    for connection in connections:
        try:
            connection.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            continue
        live.append(connection)
    return live


@pytest.fixture
def database(tmp_path: Path) -> Path:
    db = tmp_path / "flexi" / "db.db"
    run_migrations(db)
    return db


CALLS: list[tuple[str, Callable[[Path], object]]] = [
    ("describe", init_cli.describe),
    ("snapshot", snapshot),
    ("verify", snapshot),  # verified below, on the copy it makes
    ("is_initialised", setup.is_initialised),
]


@pytest.mark.parametrize(("name", "call"), CALLS, ids=[name for name, _ in CALLS])
def test_a_read_leaves_nothing_holding_the_file(
    opened: Opened, database: Path, name: str, call: Callable[[Path], object]
) -> None:
    """Whatever it opened, it closed."""
    result = call(database)
    if name == "verify":
        assert isinstance(result, Path)
        verify(result)

    assert still_open(opened) == []


def test_the_reset_can_remove_a_database_it_has_just_read(
    opened: Opened, database: Path
) -> None:
    """The whole sequence, in the order `flexi init` runs it.

    Counting the records, snapshotting them and deleting the file happen within
    a second of each other on the one path in Flexi that loses data. This is the
    failure that was reachable in practice.
    """
    init_cli.describe(database)
    taken = init_cli.reset(database)

    assert taken is not None
    assert not database.exists()
    assert still_open(opened) == []
