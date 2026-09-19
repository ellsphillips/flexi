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

import ast
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


# -- and the suite closes its own --------------------------------------------

REPO = Path(__file__).resolve().parent.parent
SOURCES = sorted((REPO / "src").rglob("*.py")) + sorted((REPO / "tests").rglob("*.py"))


def sqlite_connect(node: ast.expr) -> bool:
    """Whether an expression is a direct ``sqlite3.connect(...)`` call."""
    if not isinstance(node, ast.Call):
        return False
    called = node.func
    return (
        isinstance(called, ast.Attribute)
        and called.attr == "connect"
        and isinstance(called.value, ast.Name)
        and called.value.id == "sqlite3"
    )


def named(node: ast.expr, name: str) -> bool:
    """Whether an expression is a call to a function of this name."""
    if not isinstance(node, ast.Call):
        return False
    called = node.func
    if isinstance(called, ast.Attribute):
        return called.attr == name
    return isinstance(called, ast.Name) and called.id == name


def bare_connections(tree: ast.Module) -> list[int]:
    """Lines where a ``with`` block holds a connection nothing will close."""
    return [
        item.context_expr.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        for item in node.items
        if sqlite_connect(item.context_expr)
    ]


def borrowed_engines(tree: ast.Module) -> list[int]:
    """Lines where an engine is built inside the call that only borrows it."""
    return [
        argument.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and named(node, "get_session")
        for argument in node.args
        if named(argument, "create_db_engine")
    ]


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_with_block_holds_a_bare_connection(path: Path) -> None:
    """The rule above, read off the source instead of watched at runtime.

    A spy sees the connections the call it drives opens. This sees the ones
    nothing drives, including the suite's own: `with sqlite3.connect(...)`
    leaves a database open per test, and the warning lands on whichever test
    the garbage collector happens to be inside when it finally closes.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = bare_connections(tree)
    assert found == [], (
        f"{path.name} opens a connection in a `with` at line "
        f"{', '.join(str(line) for line in found)}; wrap it in "
        f"`contextlib.closing` and commit what it writes"
    )


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_no_engine_is_built_inside_a_borrowing_call(path: Path) -> None:
    """`get_session` takes an engine its caller disposes of.

    `get_session(create_db_engine(path))` gives that engine to nobody, so
    closing the session returns its connection to a pool that is never
    disposed and the SQLite file stays open.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = borrowed_engines(tree)
    assert found == [], (
        f"{path.name} builds an engine inside `get_session` at line "
        f"{', '.join(str(line) for line in found)}; hold it and dispose it, "
        f"or use `database_scope`"
    )
