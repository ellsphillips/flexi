"""Commands that read the database refuse until `flexi init` has configured it."""

from __future__ import annotations

import sqlite3
from contextlib import closing
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
    setup.clear_initialisation_cache()


def _run(*args: str) -> click.testing.Result:
    return click.testing.CliRunner().invoke(cli, list(args))


@pytest.mark.parametrize(
    "command",
    [["clock", "in"], ["clock", "out"], ["balance", "show"], ["balance", "log"]],
)
def test_command_needing_setup_refuses(command: list[str]) -> None:
    result = _run(*command)
    assert result.exit_code == 1
    assert "not set up" in result.output
    assert "flexi init" in result.output


@pytest.mark.parametrize(
    "command", [["--version"], ["--help"], ["clock", "--help"], ["balance", "--help"]]
)
def test_help_and_version_are_reachable_without_setup(command: list[str]) -> None:
    assert _run(*command).exit_code == 0


def test_refusing_creates_nothing(tmp_path: Path) -> None:
    _run("clock", "in")
    assert list(tmp_path.iterdir()) == []


def test_zero_byte_database_is_not_an_install(tmp_path: Path) -> None:
    """sqlite3 creates the file when it connects to a missing path."""
    (tmp_path / "db.db").touch()
    assert setup.is_initialised(tmp_path / "db.db") is False
    assert _run("balance", "show").exit_code == 1


def test_stamped_but_unconfigured_is_not_ready(tmp_path: Path) -> None:
    """Every settings getter substitutes a default, so it answers confidently."""
    db = tmp_path / "db.db"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num varchar(32))")
        connection.execute("INSERT INTO alembic_version VALUES ('0010')")
        connection.execute(
            "CREATE TABLE settings (id integer primary key, leave_year_start text,"
            " working_days text, bank_holiday_division text, auto_close_time text)"
        )
        connection.commit()

    assert setup.is_initialised(db) is False


def test_configured_database_is_ready(tmp_path: Path) -> None:
    db = tmp_path / "db.db"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num varchar(32))")
        connection.execute("INSERT INTO alembic_version VALUES ('0010')")
        connection.execute(
            "CREATE TABLE settings (id integer primary key, leave_year_start text,"
            " working_days text, bank_holiday_division text, auto_close_time text)"
        )
        connection.execute(
            "INSERT INTO settings VALUES (1,'04-06','0,1,2,3,4','scotland','18:00')"
        )
        connection.commit()

    assert setup.is_initialised(db) is True


def test_half_filled_settings_row_is_not_ready(tmp_path: Path) -> None:
    db = tmp_path / "db.db"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num varchar(32))")
        connection.execute("INSERT INTO alembic_version VALUES ('0010')")
        connection.execute(
            "CREATE TABLE settings (id integer primary key, leave_year_start text,"
            " working_days text, bank_holiday_division text, auto_close_time text)"
        )
        connection.execute("INSERT INTO settings VALUES (1,'04-06','','scotland','')")
        connection.commit()

    assert setup.is_initialised(db) is False


def test_only_a_yes_is_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An uninitialised database can become initialised; the reverse cannot."""
    db = tmp_path / "db.db"
    assert setup.is_initialised(db) is False
    db.touch()
    monkeypatch.setattr(setup, "stamped_and_configured", lambda _path: True)

    assert setup.is_initialised(db) is True


def test_bare_command_offers_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented install path is `uv tool install flexi` then `flexi`."""
    opened: list[str] = []
    monkeypatch.setattr(
        "flexi.__main__.ask_the_questions",
        lambda *_args, **_kwargs: opened.append("setup"),
    )
    monkeypatch.setattr("flexi.models.database.migrate.run_migrations", lambda: None)

    result = _run()

    assert opened == ["setup"], "it offers to set up"
    assert "not set up" not in result.output
