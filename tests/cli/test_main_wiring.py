"""The wiring in `__main__`: the setup guard, `--demo`, and the `init` menu.

The commands themselves are asserted on directly in the files beside this one;
what is left is reachable only through Click. `FlexiApp` is stood in for, so
what is checked is which database it was pointed at and whether it was opened.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import time_machine
from click.testing import CliRunner

import flexi.__main__ as main
from flexi import wallclock
from flexi.__main__ import cli
from flexi.cli import init as init_cli
from flexi.cli import ui
from flexi.locations import backups_directory, database_file
from flexi.models.database.db import AbsenceDay, BankHolidayCache, BankHolidayRefresh
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.lease import LeaseMode, database_lease
from flexi.models.database.migrate import run_migrations
from flexi.services.registry import build_services
from flexi.services.settings import parse_settings

MONDAY = datetime(2026, 8, 10, 12, 0)
"""The clock every test here runs against, so `friday` means one Friday."""

BANK_HOLIDAY = date(2026, 8, 31)
"""Summer bank holiday, England & Wales.

`AbsenceService` refuses every booking while the holiday cache is bare, so the
calendar has to answer `False` and not `None`.
"""


def set_up(db_path: Path) -> None:
    """Answer the five questions against an already-migrated database."""
    engine = create_db_engine(db_path)
    session = get_session(engine)
    services = build_services(session)
    services.settings.save_settings(
        parse_settings(
            leave_year_start="04-06",
            working_days="Mon-Fri",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    services.settings.save_entitlement(2026, 25.0)
    fetched_at = datetime(2026, 1, 1, 9, 0, tzinfo=UTC).replace(tzinfo=None)
    session.add_all(
        (
            BankHolidayRefresh(division="england-and-wales", fetched_at=fetched_at),
            BankHolidayCache(
                division="england-and-wales",
                date=BANK_HOLIDAY,
                title="Summer bank holiday",
            ),
        )
    )
    session.commit()
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _on_the_monday() -> Iterator[None]:
    with time_machine.travel(MONDAY, tick=False):
        yield


@pytest.fixture
def home() -> Path:
    """A set-up machine, under the throwaway XDG home the root conftest makes.

    Migrated through Alembic, not `create_all`: a schema built behind Alembic's
    back carries no stamp, which is the state `is_initialised` answers False for.
    """
    db = database_file()
    db.parent.mkdir(parents=True, exist_ok=True)
    run_migrations(db)
    set_up(db)
    return db


# standing in for the application


class _Opened:
    """Stands in for the application, which needs a terminal to draw to.

    Holds what `__main__` decides about it: the database path, whether the
    splash is shown, and whether it lands on the settings screen. `return_code`
    is what it decides back, and Textual sets it to 1 when a screen raises.
    """

    def __init__(self, db_path: Path | None, on_run: OnRun | None) -> None:
        self.db_path = db_path
        self.show_splash = False
        self.open_settings = False
        self.ran = False
        self.return_code: int | None = None
        self._on_run = on_run

    def run(self) -> None:
        self.ran = True
        if self._on_run is not None:
            self._on_run(self)


type OnRun = Callable[[_Opened], None]


def instead_of_the_application(
    monkeypatch: pytest.MonkeyPatch, on_run: OnRun | None = None
) -> list[_Opened]:
    """Record every application `__main__` builds, and draw none of them.

    Patched at `flexi.app.FlexiApp`: the name is imported inside `launch` and
    `run_demo`, so there is nothing bound on `__main__` to replace.
    """
    opened: list[_Opened] = []

    def building(*, db_path: Path | None = None) -> _Opened:
        app = _Opened(db_path, on_run)
        opened.append(app)
        return app

    monkeypatch.setattr("flexi.app.FlexiApp", building)
    return opened


def answering_the_questions(app: _Opened) -> None:
    """Fill the setup form in.

    `ask_the_questions` asks the database whether setup finished, not the form,
    so this writes the rows.
    """
    set_up(app.db_path or database_file())


def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a terminal, which `CliRunner` is not."""
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)


def choosing(monkeypatch: pytest.MonkeyPatch, choice: init_cli.Choice | None) -> None:
    """Pick an option at the `flexi init` menu, or escape."""
    at_a_terminal(monkeypatch)

    def picking(
        question: str,
        options: Sequence[ui.Option[init_cli.Choice]],
    ) -> ui.Option[init_cli.Choice] | None:
        if choice is None:
            return None
        return next(option for option in options if option.value == choice)

    monkeypatch.setattr("flexi.cli.ui.choose", picking)


# the sample data


def test_demo_never_opens_the_real_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at_a_terminal(monkeypatch)
    instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["--demo"])

    assert result.exit_code == 0, result.output
    assert not database_file().exists(), "the demo must not touch the real database"


def test_demo_database_is_removed_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at_a_terminal(monkeypatch)
    opened = instead_of_the_application(monkeypatch)

    CliRunner().invoke(cli, ["--demo"])

    assert opened[0].db_path is not None
    assert not opened[0].db_path.exists()


def test_demo_seeds_work_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sample database exists only while the application is up."""
    counted: list[int] = []

    def read_it(app: _Opened) -> None:
        read_only = f"file:{app.db_path}?mode=ro"
        with closing(sqlite3.connect(read_only, uri=True)) as sample:
            counted.append(
                sample.execute("SELECT count(*) FROM work_sessions").fetchone()[0]
            )

    at_a_terminal(monkeypatch)
    instead_of_the_application(monkeypatch, read_it)

    CliRunner().invoke(cli, ["--demo"])

    assert counted, "the application was never opened"
    assert counted[0] > 0


def test_demo_seeds_nothing_in_the_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--demo` hands the seed the real wall clock, not its screenshot default."""
    latest: list[str | None] = []

    def read_it(app: _Opened) -> None:
        read_only = f"file:{app.db_path}?mode=ro"
        with closing(sqlite3.connect(read_only, uri=True)) as sample:
            latest.append(
                sample.execute("SELECT max(timestamp) FROM clock_events").fetchone()[0]
            )

    at_a_terminal(monkeypatch)
    instead_of_the_application(monkeypatch, read_it)

    CliRunner().invoke(cli, ["--demo"])

    assert latest, "the application was never opened"
    assert latest[0] is not None, "the demo seeded no clock events at all"
    assert datetime.fromisoformat(latest[0]) <= wallclock.now().replace(tzinfo=None)


def test_demo_flag_rejects_a_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["--demo", "clock", "in"])

    assert result.exit_code == 2
    assert "does not take a command" in result.output
    assert opened == [], "nothing is seeded and nothing is opened"


# bare `flexi`


def test_bare_flexi_sets_up_a_new_machine() -> None:
    result = CliRunner().invoke(cli, [])

    assert database_file().is_file(), "the database was created and migrated"
    assert "not set up on this machine yet" not in result.output, (
        "bare `flexi` must not be turned away by the guard on clock and leave"
    )
    assert result.exit_code == 1
    assert "setup needs answering" in result.output
    assert "flexi init" in result.output


def test_bare_flexi_opens_after_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instead_of_the_application(monkeypatch, answering_the_questions)
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert "Flexi is set up" not in result.output, "nothing to report; it just opens"


def test_first_run_shows_the_splash(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = instead_of_the_application(monkeypatch, answering_the_questions)
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)

    CliRunner().invoke(cli, [])

    assert [app.show_splash for app in opened] == [True]


def test_closed_setup_form_leaves_the_guard_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)
    instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 1
    assert "Setup was not completed" in result.output


def test_set_up_machine_opens_without_a_splash(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at_a_terminal(monkeypatch)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert [(app.ran, app.show_splash) for app in opened] == [(True, False)]


def test_bare_flexi_without_a_terminal_refuses(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Textual draws to a pipe quite happily and never returns."""
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 1
    assert "needs a terminal" in result.output
    assert "flexi balance show" in result.output, "say what can be run instead"
    assert opened == [], "nothing is opened at a pipe"


def test_demo_without_a_terminal_refuses_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["--demo"])

    assert result.exit_code == 1
    assert "needs a terminal" in result.output
    assert opened == []


# what stopped the database being opened


def test_held_database_reports_one_line(
    home: Path,
) -> None:
    with database_lease(home, LeaseMode.EXCLUSIVE):
        result = CliRunner().invoke(cli, ["clock", "in"])

    assert result.exit_code == 1
    assert "in use at" in result.output
    assert "Traceback" not in result.output


def test_unreadable_file_names_it_and_the_backups() -> None:
    db = database_file()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"\x00 not a database " * 128)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 1
    assert str(db) in result.output
    assert str(backups_directory()) in result.output
    assert "Traceback" not in result.output


def test_corrupt_database_is_not_reported_as_missing() -> None:
    """The "not set up" advice leads to `flexi init`, which offers to erase."""
    db = database_file()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"\x00 not a database " * 128)

    result = CliRunner().invoke(cli, ["balance", "show"])

    assert result.exit_code == 1
    assert "not set up on this machine yet" not in result.output
    assert str(db) in result.output
    assert str(backups_directory()) in result.output
    assert "Traceback" not in result.output
    assert db.read_bytes().startswith(b"\x00 not a database"), "nothing was touched"


def test_torn_database_reads_as_corrupt(home: Path) -> None:
    """A half-written page raises `DatabaseError`, not "no such table"."""
    with home.open("r+b") as pages:
        pages.seek(1024)
        pages.write(b"\xff" * 4096)

    result = CliRunner().invoke(cli, ["balance", "show"])

    assert result.exit_code == 1
    assert "not set up on this machine yet" not in result.output
    assert str(backups_directory()) in result.output
    assert "Traceback" not in result.output


def test_directory_in_the_database_path_is_reported() -> None:
    db = database_file()
    db.mkdir(parents=True)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 1
    assert str(db) in result.output
    assert str(backups_directory()) in result.output
    assert "Traceback" not in result.output


def test_unstamped_schema_names_the_database() -> None:
    """Alembic refuses to upgrade a schema it never stamped."""
    db = database_file()
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE settings (id integer primary key)")
        connection.commit()

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 1
    assert str(db) in result.output
    assert "Traceback" not in result.output


def test_unmakeable_data_directory_is_reported() -> None:
    directory = database_file().parent
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.write_text("in the way", encoding="utf-8")

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 1
    assert str(directory) in result.output
    assert "Traceback" not in result.output


def test_unknown_revision_suggests_an_upgrade(home: Path) -> None:
    """The refusal names the file once."""
    with closing(sqlite3.connect(home)) as connection:
        connection.execute("UPDATE alembic_version SET version_num = '0099'")
        connection.commit()

    result = CliRunner().invoke(cli, ["balance", "show"])

    assert result.exit_code == 1
    assert "newer Flexi" in result.output
    assert result.output.count(str(home)) == 1
    assert "Traceback" not in result.output


def test_unrelated_fault_keeps_its_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the actionable failures get a sentence; a bug keeps its traceback."""

    def bug() -> None:
        msg = "something went wrong deep inside"
        raise ValueError(msg)

    monkeypatch.setattr("flexi.models.database.migrate.run_migrations", bug)

    result = CliRunner().invoke(cli, ["init"])

    assert isinstance(result.exception, ValueError)


# what the shell is told


def crashing(app: _Opened) -> None:
    """Textual's return code when a screen raises."""
    app.return_code = 1


@pytest.mark.parametrize(
    ("command", "arrange"),
    [
        pytest.param([], "set-up", id="bare flexi"),
        pytest.param(["--demo"], "none", id="the demo"),
        pytest.param(["init"], "menu", id="open, from the init menu"),
    ],
)
def test_crashed_application_exits_nonzero(
    command: list[str],
    arrange: str,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    at_a_terminal(monkeypatch)
    if arrange != "none":
        request.getfixturevalue("home")
    if arrange == "menu":
        choosing(monkeypatch, init_cli.Choice.OPEN)
    instead_of_the_application(monkeypatch, crashing)

    result = CliRunner().invoke(cli, command)

    assert result.exit_code == 1, result.output


def test_crashed_setup_form_is_not_reported_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at_a_terminal(monkeypatch)
    instead_of_the_application(monkeypatch, crashing)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 1
    assert "Setup was not completed" not in result.output


# the guard


@pytest.mark.parametrize(
    "command",
    [
        ["balance", "show"],
        ["balance", "log"],
        ["clock", "in"],
        ["leave", "annual", "friday"],
        ["holidays", "refresh"],
    ],
)
def test_command_before_setup_is_refused(
    command: list[str],
) -> None:
    result = CliRunner().invoke(cli, command)

    assert result.exit_code == 1
    assert "not set up on this machine yet" in result.output
    assert "flexi init" in result.output
    assert not database_file().exists(), "refusing must not leave a database behind"


def test_ignored_preferences_go_to_stderr(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("flexi.config.CONFIG_PROBLEM", "config.yaml could not be used")

    result = CliRunner().invoke(cli, ["balance", "show"])

    assert result.exit_code == 0, result.output
    assert "config.yaml could not be used" in result.stderr
    assert "config.yaml could not be used" not in result.stdout, "not the output"


def test_help_works_without_a_database() -> None:
    """The guard is per command.

    On the group it would run before Click resolves the subcommand, refusing
    `flexi init` on the machine that needs it and turning `flexi clock --help`
    into an error message about setup.
    """
    result = CliRunner().invoke(cli, ["clock", "--help"])

    assert result.exit_code == 0, result.output
    assert "Clock in or out" in result.output


# the commands, wired up


def test_clocking_in_from_the_command_line(home: Path) -> None:
    result = CliRunner().invoke(cli, ["clock", "in"])

    assert result.exit_code == 0, result.output
    assert "Clocked in" in result.output


def test_refreshing_the_calendar_asks_gov_uk_once(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening the database fills an empty cache, and so does this command."""
    engine = create_db_engine(home)
    session = get_session(engine)
    session.query(BankHolidayCache).delete()
    session.query(BankHolidayRefresh).delete()
    session.commit()
    session.close()
    engine.dispose()

    asked: list[str] = []

    def counted(*_args: object, **_kwargs: object) -> None:
        asked.append("gov.uk")
        msg = "the test suite does not make network requests"
        raise httpx.ConnectError(msg)

    monkeypatch.setattr(httpx.Client, "send", counted)

    CliRunner().invoke(cli, ["holidays", "refresh"])

    assert len(asked) == 1


def test_empty_calendar_is_fetched_once(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_db_engine(home)
    session = get_session(engine)
    session.query(BankHolidayCache).delete()
    session.query(BankHolidayRefresh).delete()
    session.commit()
    session.close()
    engine.dispose()

    asked: list[str] = []

    def counted(*_args: object, **_kwargs: object) -> None:
        asked.append("gov.uk")
        msg = "the test suite does not make network requests"
        raise httpx.ConnectError(msg)

    monkeypatch.setattr(httpx.Client, "send", counted)

    CliRunner().invoke(cli, ["balance", "show"])
    CliRunner().invoke(cli, ["balance", "show"])

    assert len(asked) == 1


def test_refreshing_offline_fails(
    home: Path,
) -> None:
    result = CliRunner().invoke(cli, ["holidays", "refresh"])

    assert result.exit_code == 1
    assert "Could not reach GOV.UK" in result.output


def test_dry_run_shows_the_plan_and_writes_nothing(home: Path) -> None:
    result = CliRunner().invoke(cli, ["leave", "annual", "friday", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Booking annual leave" in result.output
    assert "14 Aug" in result.output
    assert booked_days(home) == []


def test_leave_books_the_days_it_showed(home: Path) -> None:
    result = CliRunner().invoke(cli, ["leave", "annual", "friday", "--yes"])

    assert result.exit_code == 0, result.output
    assert booked_days(home) == [date(2026, 8, 14)]


def test_other_leave_keeps_its_note(home: Path) -> None:
    result = CliRunner().invoke(
        cli, ["leave", "other", "friday", "--note", "jury service", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert notes(home) == ["jury service"]


def test_invalid_utf8_note_is_refused_early(
    home: Path,
) -> None:
    """A cp1252 paste arrives from argv as a lone surrogate, which SQLite refuses."""
    result = CliRunner().invoke(
        cli, ["leave", "other", "friday", "--note", "caf\udce9", "--yes"]
    )

    assert result.exit_code == 2
    assert "not valid UTF-8" in result.output
    assert "Booking other leave" not in result.output
    assert booked_days(home) == []


def test_confirmation_is_asked_on_stderr(
    home: Path,
) -> None:
    """`flexi leave annual friday > plan.txt` keeps the question out of the file."""
    result = CliRunner().invoke(cli, ["leave", "annual", "friday"], input="n\n")

    assert result.exit_code == 1
    assert "Book it?" in result.stderr
    assert "Book it?" not in result.stdout
    assert "Booking annual leave" in result.stdout, "the plan is the output"


def booked_days(db_path: Path) -> list[date]:
    engine = create_db_engine(db_path)
    session = get_session(engine)
    try:
        return [row.date for row in session.query(AbsenceDay).order_by(AbsenceDay.date)]
    finally:
        session.close()
        engine.dispose()


def notes(db_path: Path) -> list[str | None]:
    engine = create_db_engine(db_path)
    session = get_session(engine)
    try:
        return [row.note for row in session.query(AbsenceDay)]
    finally:
        session.close()
        engine.dispose()


# `flexi init` on a machine with nothing on it


def test_init_without_a_terminal_reports_progress() -> None:
    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 1
    assert "The database is ready at" in result.output
    assert "Run `flexi init` from a terminal to finish." in result.output


def test_init_reports_where_the_records_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup stops once the questions are answered; bare `flexi` carries on."""
    opened = instead_of_the_application(monkeypatch, answering_the_questions)
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert f"Flexi is set up. Its records are at {database_file()}" in result.output
    assert [app.show_splash for app in opened] == [True]


def test_migration_that_completes_setup_asks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database with answers but no stamp reads as not set up until migrated."""

    def migrating(db_path: Path | None = None) -> None:
        target = db_path or database_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        run_migrations(target)
        set_up(target)

    monkeypatch.setattr("flexi.models.database.migrate.run_migrations", migrating)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert "Flexi is set up." in result.output
    assert opened == [], "the five questions are not asked over existing answers"


# `flexi init` on a machine that already has records


def test_init_without_a_terminal_reports_and_stops(
    home: Path,
) -> None:
    """Erasing records needs a person to type the word; no flag stands in."""
    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert str(home) in result.output
    assert "from a terminal to change or reset them" in result.output
    assert home.is_file()


def test_open_from_the_menu_opens_the_records(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choosing(monkeypatch, init_cli.Choice.OPEN)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert [(app.ran, app.open_settings) for app in opened] == [(True, False)]
    assert home.is_file()


def test_settings_from_the_menu_opens_settings(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choosing(monkeypatch, init_cli.Choice.SETTINGS)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert [app.open_settings for app in opened] == [True]


def test_escaping_the_menu_changes_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choosing(monkeypatch, None)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert opened == []
    assert home.is_file()


def test_declining_the_last_gate_erases_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choosing(monkeypatch, init_cli.Choice.RESET)
    monkeypatch.setattr("flexi.cli.ui.type_the_word", lambda *_a, **_k: False)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert "Nothing was erased." in result.output
    assert home.is_file()
    assert opened == [], "nor is the setup form opened over records still there"


def test_reset_keeps_a_snapshot_then_asks_again(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choosing(monkeypatch, init_cli.Choice.RESET)
    monkeypatch.setattr("flexi.cli.ui.type_the_word", lambda *_a, **_k: True)
    opened = instead_of_the_application(monkeypatch, answering_the_questions)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert "Erased. Snapshot kept at" in result.output
    assert [app.show_splash for app in opened] == [True], "a first run all over again"
    assert list(backups_directory().glob("*.bak")), "the only way back"


def test_reset_forgets_the_memoised_setup(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`is_initialised` memoises the affirmative, so the reset has to forget it."""
    choosing(monkeypatch, init_cli.Choice.RESET)
    monkeypatch.setattr("flexi.cli.ui.type_the_word", lambda *_a, **_k: True)
    instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 1
    assert "Setup was not completed" in result.output


def test_erasing_an_absent_database_takes_no_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`reset` answers `None` when there is no file to copy."""
    main.erase(tmp_path / "absent.db")

    assert "Snapshot" not in capsys.readouterr().err


def test_leave_examples_are_listed_one_per_line() -> None:
    r"""Click's no-rewrap marker is a backspace character, not a backslash.

    In a raw docstring `\b` is two characters Click does not recognise, and the
    five examples are rewrapped into a paragraph. D301 is silenced there, and
    neither side of that shows from inside the module.
    """
    output = CliRunner().invoke(cli, ["leave", "--help"]).output

    assert "\\b" not in output
    assert "\b" not in output
    for example in ("flexi leave annual friday", "flexi leave sick today pm"):
        assert f"\n  {example}\n" in output, output
