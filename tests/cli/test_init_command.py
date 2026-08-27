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
from flexi.cli import ui
from flexi.models.database.backup import snapshot, verify
from flexi.models.database.db import Base
from flexi.models.database.engine import create_db_engine, get_session
from flexi.services.registry import Services


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
        clock = Services.build(session).clock
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
    """`yes | flexi init` must never answer for a person."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert ui.interactive() is False


def test_a_database_that_cannot_be_read_is_not_reported_as_empty(
    tmp_path: Path,
) -> None:
    """Absent means nothing to lose. Unreadable means nobody knows.

    Collapsing the two is how a confirmation ends up printing "nothing recorded
    yet" over a file that is merely locked by the application in another window.
    """
    rubbish = tmp_path / "not-a-database.db"
    rubbish.write_bytes(b"this is not a SQLite file at all, not even close")

    contents = init_cli.describe(rubbish)

    assert contents.unreadable
    assert not contents.is_empty, "it must not claim there is nothing to lose"


def test_an_absent_database_is_empty_rather_than_unreadable(tmp_path: Path) -> None:
    contents = init_cli.describe(tmp_path / "absent.db")
    assert contents.is_empty
    assert not contents.unreadable


def erase_option(contents: init_cli.Contents) -> ui.Option:
    options = init_cli.options(contents)
    return next(o for o in options if o.value == init_cli.Choice.RESET)


def test_the_menu_says_how_much_it_would_erase() -> None:
    """A number somebody recognises is what separates reading from skimming."""
    assert erase_option(init_cli.Contents((("days", 1),))).hint == "erase 1 record"
    assert erase_option(init_cli.Contents((("days", 9),))).hint == "erase 9 records"
    assert erase_option(init_cli.Contents()).hint == "erase everything"


def test_the_destructive_row_is_drawn_in_the_deficit_red(populated: Path) -> None:
    assert erase_option(init_cli.describe(populated)).grave


def test_the_safe_option_is_first(populated: Path) -> None:
    """Enter on arrival must never be the keystroke that erases anything."""
    first = init_cli.options(init_cli.describe(populated))[0]
    assert first.value == init_cli.Choice.OPEN
    assert not first.grave


def test_the_overview_lists_what_is_there(populated: Path) -> None:
    drawn = "\n".join(
        line.plain
        for line in init_cli.overview(populated, init_cli.describe(populated))
    )
    assert "clock events" in drawn
    assert str(populated) in drawn


def test_a_table_this_list_has_not_heard_of_does_not_blank_the_count(
    tmp_path: Path,
) -> None:
    """`COUNTED` is maintained by hand, and schemas move.

    A table renamed or not yet added by a migration arrives as the same
    `DatabaseError` as a corrupt file. Forgiving it is what keeps a reset
    prompt honest about the tables that are still there, rather than reporting
    the whole database as holding nothing.
    """
    db = tmp_path / "older.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE clock_events (id integer primary key)")
        connection.execute("INSERT INTO clock_events VALUES (1)")

    contents = init_cli.describe(db)

    assert contents.counts == (("clock events", 1),)
    assert not contents.unreadable, "a missing table is not an unreadable file"


# -- what the rail says ------------------------------------------------------


def test_the_overview_of_an_unreadable_database_does_not_promise_it_is_empty(
    tmp_path: Path,
) -> None:
    """An unreadable file is not an empty one, and the rail has to say so.

    "Nothing recorded yet" drawn over a database that is merely locked by the
    application in another window is how somebody agrees to lose a year.
    """
    drawn = [
        line.plain
        for line in init_cli.overview(
            tmp_path / "locked.db", init_cli.Contents(unreadable=True)
        )
    ]

    assert any("could not be read" in line for line in drawn)
    assert any("may still hold records" in line for line in drawn)


def test_the_overview_of_an_empty_database_says_so(tmp_path: Path) -> None:
    """There is genuinely nothing to lose here.

    Saying so plainly is what stops the reset row further down reading as a
    threat on a machine that has never recorded anything.
    """
    drawn = [
        line.plain
        for line in init_cli.overview(tmp_path / "empty.db", init_cli.Contents())
    ]

    assert any("Nothing recorded yet" in line for line in drawn)


def test_choosing_from_the_menu_returns_what_was_chosen(
    populated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The overview is on screen before the question is asked.

    Offering "Start again" above a blank terminal asks somebody to decide
    about records they have not been shown.
    """
    asked: list[str] = []

    def picking(question: str, options: list[ui.Option]) -> ui.Option:
        asked.append(question)
        return next(o for o in options if o.value == init_cli.Choice.SETTINGS)

    monkeypatch.setattr("flexi.cli.ui.choose", picking)

    chosen = init_cli.ask(populated, init_cli.describe(populated))

    assert chosen is init_cli.Choice.SETTINGS
    assert asked == ["What would you like to do?"]
    assert "clock events" in capsys.readouterr().err


def test_escaping_the_menu_chooses_nothing(
    populated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escape has to mean escape on the one menu that can erase records."""
    monkeypatch.setattr("flexi.cli.ui.choose", lambda *_a, **_k: None)

    assert init_cli.ask(populated, init_cli.describe(populated)) is None


def test_the_last_gate_asks_for_the_word_rather_than_a_keystroke(
    populated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A keystroke can be muscle memory. Spelling it out is agreement."""
    required: list[str] = []

    def typing(word: str, question: str) -> bool:
        required.append(word)
        assert word in question, "the prompt must say which word it wants"
        return True

    monkeypatch.setattr("flexi.cli.ui.type_the_word", typing)

    assert init_cli.confirm_reset(init_cli.describe(populated))

    assert required == ["reset"]
    shown = capsys.readouterr().err
    assert "cannot be undone" in shown
    assert "clock events" in shown, "it must name what it is about to take"
    assert "snapshot" in shown, "and where the one way back is written"


def test_the_last_gate_can_be_declined(
    populated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("flexi.cli.ui.type_the_word", lambda *_a, **_k: False)

    assert not init_cli.confirm_reset(init_cli.describe(populated))


def test_the_gate_over_an_unreadable_database_admits_it_cannot_list_the_loss(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Listing nothing would read as a promise that nothing is there."""
    monkeypatch.setattr("flexi.cli.ui.type_the_word", lambda *_a, **_k: True)

    init_cli.confirm_reset(init_cli.Contents(unreadable=True))

    shown = capsys.readouterr().err
    assert "could not be read" in shown
    assert "may hold more than is listed here" in shown


def test_the_rail_is_closed_off_with_what_happened(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A transcript of a reset should end with where the snapshot went."""
    init_cli.settled("Erased. Snapshot kept at /tmp/snap.bak")

    assert "Erased. Snapshot kept at /tmp/snap.bak" in capsys.readouterr().err
