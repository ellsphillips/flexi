"""The one command in Flexi that loses data.

A reset takes a snapshot first and removes only the database file. The backups
directory lives inside the data directory, so deleting the directory would take
every snapshot ever made -- including the one taken a moment earlier, which is
the entire safety net.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flexi.cli import init as init_cli
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.backup import snapshot, verify
from flexi.models.database.db import Base
from flexi.services.clock import ClockService


@pytest.fixture
def populated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A database with records in it, and the backups directory beside it."""
    data = tmp_path / "flexi"
    data.mkdir()
    monkeypatch.setattr("flexi.locations.data_directory", lambda: data)
    monkeypatch.setattr(
        "flexi.models.database.backup.backups_directory", lambda: data / "backups"
    )

    db = data / "db.db"
    engine = create_db_engine(db)
    Base.metadata.create_all(engine)
    with get_session(engine) as session:
        session.execute(
            __import__("sqlalchemy").text(
                "CREATE TABLE alembic_version (version_num varchar(32))"
            )
        )
        session.execute(
            __import__("sqlalchemy").text("INSERT INTO alembic_version VALUES ('0010')")
        )
        session.commit()
        clock = ClockService(session)
        clock.clock_in()
    engine.dispose()
    return db


def test_a_snapshot_is_consistent_and_verifies(populated: Path) -> None:
    taken = snapshot(populated)
    assert taken.is_file()
    assert verify(taken)


def test_a_snapshot_holds_what_the_database_held(populated: Path) -> None:
    taken = snapshot(populated)
    with sqlite3.connect(f"file:{taken}?mode=ro", uri=True) as copy:
        events = copy.execute("SELECT count(*) FROM clock_events").fetchone()[0]
    assert events == 1


def test_two_snapshots_in_the_same_second_do_not_collide(populated: Path) -> None:
    """The migration backups use one-second stamps, and a reset is two at once."""
    first = snapshot(populated)
    second = snapshot(populated)
    assert first != second
    assert first.is_file()
    assert second.is_file()


def test_a_reset_removes_the_database_and_keeps_the_snapshot(populated: Path) -> None:
    taken = init_cli.reset(populated)

    assert not populated.exists(), "the records are gone"
    assert taken is not None
    assert taken.is_file(), "the snapshot is not"
    assert verify(taken)


def test_a_reset_does_not_touch_the_backups_directory(populated: Path) -> None:
    """Deleting the data directory would take the safety net with it."""
    earlier = snapshot(populated, prefix="migration_")
    init_cli.reset(populated)
    assert earlier.is_file()


def test_a_reset_of_a_missing_database_takes_no_snapshot(tmp_path: Path) -> None:
    assert init_cli.reset(tmp_path / "absent.db") is None


def test_what_a_reset_would_take_is_counted(populated: Path) -> None:
    contents = init_cli.describe(populated)
    assert not contents.is_empty
    assert ("clock events", 1) in contents.counts


def test_an_empty_database_is_described_as_empty(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    engine = create_db_engine(db)
    Base.metadata.create_all(engine)
    engine.dispose()
    assert init_cli.describe(db).is_empty


def test_a_missing_database_describes_as_empty(tmp_path: Path) -> None:
    assert init_cli.describe(tmp_path / "absent.db").is_empty


def test_a_torn_snapshot_stops_the_reset(
    populated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the copy cannot be trusted, the original is not removed."""
    monkeypatch.setattr("flexi.cli.init.verify", lambda _: False)

    import click

    with pytest.raises(click.ClickException, match="did not verify"):
        init_cli.reset(populated)

    assert populated.is_file(), "nothing is deleted when the snapshot is suspect"


def test_a_pipe_is_not_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """`yes | flexi init --reset` must never answer for a person."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert init_cli.interactive() is False
