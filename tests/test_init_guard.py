"""Refusing to guess, on a machine that has not been set up.

`flexi balance show` on a migrated-but-unconfigured database used to print a
deficit of 1161 hours: every settings getter substitutes a default, so the
figure was computed from a 1 January leave year and a 7h24 day nobody chose,
and presented as fact.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click.testing
import pytest

from flexi.__main__ import cli
from flexi.services import setup


@pytest.fixture(autouse=True)
def _elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every location at an empty directory, and clear the memo."""
    monkeypatch.setattr("flexi.locations.database_file", lambda: tmp_path / "db.db")
    monkeypatch.setattr(
        "flexi.services.setup.database_file", lambda: tmp_path / "db.db"
    )
    setup._INITIALISED.clear()


def _run(*args: str) -> click.testing.Result:
    return click.testing.CliRunner().invoke(cli, list(args))


@pytest.mark.parametrize(
    "command",
    [["clock", "in"], ["clock", "out"], ["balance", "show"], ["balance", "log"]],
)
def test_a_command_that_needs_setup_refuses_before_it(command: list[str]) -> None:
    result = _run(*command)
    assert result.exit_code == 1
    assert "not set up" in result.output
    assert "flexi init" in result.output


@pytest.mark.parametrize(
    "command", [["--version"], ["--help"], ["clock", "--help"], ["balance", "--help"]]
)
def test_help_and_version_are_reachable_without_setup(command: list[str]) -> None:
    """Otherwise the instructions are behind the thing they instruct you to do."""
    assert _run(*command).exit_code == 0


def test_refusing_creates_nothing(tmp_path: Path) -> None:
    _run("clock", "in")
    assert list(tmp_path.iterdir()) == []


def test_a_zero_byte_database_is_not_an_install(tmp_path: Path) -> None:
    """Connecting to a missing SQLite path leaves one behind. It is not a Flexi."""
    (tmp_path / "db.db").touch()
    assert setup.is_initialised(tmp_path / "db.db") is False
    assert _run("balance", "show").exit_code == 1


def test_a_stamped_but_unconfigured_database_is_not_ready(tmp_path: Path) -> None:
    """The -1161:48 bug: every getter has a default, so it answers confidently."""
    db = tmp_path / "db.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num varchar(32))")
        connection.execute("INSERT INTO alembic_version VALUES ('0010')")
        connection.execute(
            "CREATE TABLE settings (id integer primary key, leave_year_start text,"
            " working_days text, bank_holiday_division text, auto_close_time text)"
        )

    assert setup.is_initialised(db) is False


def test_a_configured_database_is_ready(tmp_path: Path) -> None:
    db = tmp_path / "db.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num varchar(32))")
        connection.execute("INSERT INTO alembic_version VALUES ('0010')")
        connection.execute(
            "CREATE TABLE settings (id integer primary key, leave_year_start text,"
            " working_days text, bank_holiday_division text, auto_close_time text)"
        )
        connection.execute(
            "INSERT INTO settings VALUES (1,'04-06','0,1,2,3,4','scotland','18:00')"
        )

    assert setup.is_initialised(db) is True


def test_a_half_filled_settings_row_is_not_ready(tmp_path: Path) -> None:
    db = tmp_path / "db.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num varchar(32))")
        connection.execute("INSERT INTO alembic_version VALUES ('0010')")
        connection.execute(
            "CREATE TABLE settings (id integer primary key, leave_year_start text,"
            " working_days text, bank_holiday_division text, auto_close_time text)"
        )
        connection.execute("INSERT INTO settings VALUES (1,'04-06','','scotland','')")

    assert setup.is_initialised(db) is False


def test_the_answer_is_remembered_but_only_when_it_is_yes(tmp_path: Path) -> None:
    """False can become true at any moment; nothing should have to invalidate it."""
    db = tmp_path / "db.db"
    assert setup.is_initialised(db) is False
    assert db not in setup._INITIALISED
