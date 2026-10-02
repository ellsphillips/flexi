"""The wiring in `__main__`: the setup guard, `--demo`, and the `init` menu.

The commands themselves are asserted on directly in the files beside this one;
what is left is reachable only through Click. `FlexiApp` is stood in for, so
what is checked is which database it was pointed at and whether it was opened.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import click
import httpx
import pytest
import time_machine
from click.testing import CliRunner

import flexi.__main__ as main
from flexi import wallclock
from flexi.__main__ import LEFT_RUNNING, cli
from flexi.cli import init as init_cli
from flexi.cli import ui
from flexi.cli.leave import parse_request
from flexi.domain.dates import Preference, parse_span
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
    """Answer the six questions against an already-migrated database."""
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
        self.demo = False
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


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGHUP")
@pytest.mark.parametrize("name", ["SIGHUP", "SIGTERM"])
def test_demo_database_is_removed_when_the_window_closes(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Closing the window sends SIGHUP, and `kill` sends SIGTERM.

    Either ends the process where it stands by default, leaving the sample
    database behind. A stand-in takes that default's place here, as the default
    would end the test run itself.
    """
    signum: int = getattr(signal, name)
    caught: list[int] = []

    def stand_in(received: int, _frame: object) -> None:
        caught.append(received)

    at_a_terminal(monkeypatch)
    opened = instead_of_the_application(
        monkeypatch, lambda _app: os.kill(os.getpid(), signum)
    )
    original = signal.signal(signum, stand_in)
    try:
        with pytest.raises(SystemExit) as ended:
            main.run_demo(click.Context(cli))
        restored = signal.getsignal(signum)
    finally:
        signal.signal(signum, original)

    assert ended.value.code == 128 + signum, "the status a shell gives the signal"
    assert opened[0].db_path is not None
    assert not opened[0].db_path.parent.exists()
    assert caught == []
    assert restored is stand_in, "the handler from before the demo is put back"


def test_demo_is_opened_as_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """So the application can say the records are samples, and go when it does."""
    at_a_terminal(monkeypatch)
    opened = instead_of_the_application(monkeypatch)

    CliRunner().invoke(cli, ["--demo"])

    assert [app.demo for app in opened] == [True]


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


def test_closed_setup_form_says_nothing_was_kept_and_how_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named by no command: a visitor who ran `uvx flexi` has no `flexi`."""
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)
    instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, [])

    assert "none of your answers were saved" in result.stderr
    assert "Run Flexi again to finish it" in result.stderr
    assert str(database_file()) in result.stderr


def test_set_up_machine_opens_without_a_splash(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at_a_terminal(monkeypatch)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert [(app.ran, app.show_splash) for app in opened] == [(True, False)]


@pytest.mark.parametrize(
    ("command", "said"),
    [([], "Opening Flexi…"), (["--demo"], "Opening the Flexi demo with sample data…")],
    ids=["flexi", "demo"],
)
def test_the_application_says_it_is_opening(
    home: Path, monkeypatch: pytest.MonkeyPatch, command: list[str], said: str
) -> None:
    """A first launch spends seconds importing, and a blank terminal looks hung."""
    at_a_terminal(monkeypatch)
    instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, command)

    assert result.exit_code == 0, result.output
    assert said in result.stderr


def test_a_command_says_nothing_of_opening(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at_a_terminal(monkeypatch)

    result = CliRunner().invoke(cli, ["balance", "show"])

    assert result.exit_code == 0, result.output
    assert "Opening" not in result.output


def test_nothing_is_said_of_opening_without_a_terminal(home: Path) -> None:
    assert "Opening" not in CliRunner().invoke(cli, []).output


def test_opening_is_said_before_the_slow_imports() -> None:
    """Pydantic, SQLAlchemy and Textual are what a first launch waits on.

    A fresh interpreter refuses to import them at all, so saying it late fails
    at once and never opens the application.
    """
    script = """
import sys

import click

import flexi.cli.ui


class Refused:
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in {"alembic", "pydantic", "sqlalchemy", "textual"}:
            raise AssertionError(f"{name} was imported before anything was said")


def said(message, *_args, **_kwargs):
    sys.exit(0 if "Opening" in str(message) else f"{message!r} was said first")


flexi.cli.ui.interactive = lambda: True
click.secho = said
sys.meta_path.insert(0, Refused())

from flexi.__main__ import cli

cli([], prog_name="flexi")
"""
    finished = subprocess.run(  # noqa: S603 - fixed interpreter and in-repository script
        [sys.executable, "-c", script],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )

    assert finished.returncode == 0, finished.stderr


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
    assert "needs an interactive terminal" in result.output
    assert opened == []


def test_demo_without_a_terminal_names_no_command() -> None:
    """A visitor who ran `uvx flexi --demo` has no `flexi` to run anything with.

    Nor would `flexi balance show` read the demo's records.
    """
    result = CliRunner().invoke(cli, ["--demo"])

    assert "`flexi" not in result.output


@pytest.mark.parametrize(
    "message", [main.NEEDS_TERMINAL, main.DEMO_NEEDS_TERMINAL], ids=["app", "demo"]
)
@pytest.mark.parametrize(
    ("platform", "pointed"), [("win32", True), ("darwin", False), ("linux", False)]
)
def test_only_windows_is_told_which_consoles_will_do(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    message: str,
    platform: str,
    pointed: bool,
) -> None:
    """Git Bash's mintty and an IDE's output pane are no console to draw in.

    The platform is pinned, as the suite runs on all three.
    """
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: False)
    monkeypatch.setattr(sys, "platform", platform)

    with pytest.raises(click.exceptions.Exit) as refused:
        main.needs_a_terminal(click.Context(cli), message)

    assert refused.value.exit_code == 1
    said = capsys.readouterr().err
    assert said.startswith(message)
    assert (
        "Run Flexi in Windows Terminal, PowerShell or Command Prompt." in said
    ) is pointed


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


def test_the_guard_points_at_flexi_itself() -> None:
    """The README describes `flexi init` only as a reset, so `flexi` comes first."""
    result = CliRunner().invoke(cli, ["clock", "in"])

    assert "Run `flexi` (or `flexi init`) to choose" in result.stderr


def test_ignored_preferences_go_to_stderr(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("flexi.config.CONFIG_PROBLEM", "config.yaml could not be used")

    result = CliRunner().invoke(cli, ["balance", "show"])

    assert result.exit_code == 0, result.output
    assert "config.yaml could not be used" in result.stderr
    assert "config.yaml could not be used" not in result.stdout, "not the output"


@pytest.mark.parametrize(
    "command", [[], ["leave"], ["balance", "adjust"]], ids=["flexi", "leave", "adjust"]
)
def test_h_is_help(command: list[str]) -> None:
    """Many people type -h first, and it should not be their first error.

    The two commands that let unknown options through as words included.
    """
    short = CliRunner().invoke(cli, [*command, "-h"])

    assert short.exit_code == 0, short.output
    assert short.output == CliRunner().invoke(cli, [*command, "--help"]).output


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


@pytest.mark.parametrize("command", [["clock", "out"], ["balance", "show"]])
def test_every_command_says_what_the_sweep_closed(
    home: Path, command: list[str]
) -> None:
    """Friday was left running, and whichever command runs next closes it.

    Said on stderr: it explains the next morning's `Not clocked in`, and it is
    not the output of `balance show`.
    """
    with time_machine.travel(datetime(2026, 8, 7, 9, 0), tick=False):
        assert CliRunner().invoke(cli, ["clock", "in"]).exit_code == 0

    result = CliRunner().invoke(cli, command)

    assert (
        LEFT_RUNNING.format(
            closed="Fri 7 Aug was left running and closed at 18:00 (9:00 counted)"
        )
        in result.stderr
    )
    assert "left running" not in result.stdout
    assert "left running" not in CliRunner().invoke(cli, command).output, "once"


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


@pytest.mark.parametrize(
    ("typed", "said"),
    [
        ("--dryrun", "No such option '--dryrun'. Did you mean '--dry-run'?"),
        ("--yse", "No such option '--yse'. Did you mean '--yes'?"),
        ("-y", "No such option '-y'."),
    ],
)
def test_a_mistyped_option_is_not_blamed_on_the_date(
    home: Path, typed: str, said: str
) -> None:
    """Unknown options are let through as words, so that `-2w` is a date."""
    result = CliRunner().invoke(cli, ["leave", "annual", "friday", typed])

    assert result.exit_code == 2
    assert said in result.stderr
    assert "Try 2026-06-12" not in result.output
    assert booked_days(home) == []


def test_completing_after_a_mistyped_option_refuses_nothing() -> None:
    """Completion parses what it can, and a refusal there is a traceback."""
    result = CliRunner().invoke(
        cli,
        prog_name="flexi",
        env={
            "_FLEXI_COMPLETE": "zsh_complete",
            "COMP_WORDS": "flexi leave annual --dryrun ",
            "COMP_CWORD": "4",
        },
    )

    assert result.exit_code == 0, repr(result.exception)


def test_a_word_after_a_double_dash_is_read_as_a_word(home: Path) -> None:
    """`--` ends the options, so what follows it is the date, however spelled."""
    result = CliRunner().invoke(cli, ["leave", "annual", "friday", "--", "--dry-run"])

    assert result.exit_code == 2
    assert "No such option" not in result.output
    assert "Try 2026-06-12" in result.output


def test_a_negative_offset_is_still_a_date(home: Path) -> None:
    result = CliRunner().invoke(cli, ["leave", "annual", "-3d", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Fri 7 Aug" in result.output


def test_a_date_with_an_h_in_it_is_not_a_call_for_help(home: Path) -> None:
    """Help exits 0, which a script booking with --yes would take for booked."""
    result = CliRunner().invoke(cli, ["leave", "sick", "-1h", "--yes"])

    assert result.exit_code == 2
    assert "Try 2026-06-12" in result.output
    assert booked_days(home) == []


def test_leave_with_nothing_to_answer_says_to_add_yes(home: Path) -> None:
    """Cron and Task Scheduler give no input, and get what to add, not "Aborted!"."""
    result = CliRunner().invoke(cli, ["leave", "annual", "friday"], input="")

    assert result.exit_code == 1
    assert "add --yes to book it without asking" in result.stderr
    assert "Aborted!" not in result.output
    assert booked_days(home) == []


def test_cancelling_with_nothing_to_answer_says_to_add_yes(home: Path) -> None:
    CliRunner().invoke(cli, ["leave", "annual", "friday", "--yes"])

    result = CliRunner().invoke(cli, ["leave", "cancel", "friday"], input="")

    assert result.exit_code == 1
    assert "add --yes to cancel them without asking" in result.stderr
    assert "Aborted!" not in result.output
    assert booked_days(home) == [date(2026, 8, 14)]


def test_a_piped_answer_is_read_like_a_typed_one(home: Path) -> None:
    """`echo y | flexi leave annual friday` books, and cancels, as typing y does."""
    booked = CliRunner().invoke(cli, ["leave", "annual", "friday"], input="y\n")
    assert booked.exit_code == 0, booked.output
    assert booked_days(home) == [date(2026, 8, 14)]

    cancelled = CliRunner().invoke(cli, ["leave", "cancel", "friday"], input="y\n")
    assert cancelled.exit_code == 0, cancelled.output
    assert booked_days(home) == []


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
    assert opened == [], "the six questions are not asked over existing answers"


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


def test_the_snapshot_path_is_printed_whole(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a narrow terminal too: the snapshot holds everything that was erased."""
    monkeypatch.setenv("COLUMNS", "40")
    choosing(monkeypatch, init_cli.Choice.RESET)
    monkeypatch.setattr("flexi.cli.ui.type_the_word", lambda *_a, **_k: True)
    instead_of_the_application(monkeypatch, answering_the_questions)

    result = CliRunner().invoke(cli, ["init"])

    (taken,) = backups_directory().glob("pre-init*.bak")
    assert str(taken) in result.output


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


def test_every_leave_example_works_all_year() -> None:
    """A fixed date such as `12 jun` drifts into next year, and onto a weekend.

    So each example names a day in the fortnight ahead, on whatever day the help
    is read.
    """
    output = CliRunner().invoke(cli, ["leave", "--help"]).output
    examples = [
        line.split()[2:]
        for line in output.splitlines()
        if line.strip().startswith("flexi leave ")
    ]
    assert examples

    for offset in range(366):
        today = date(2026, 1, 1) + timedelta(days=offset)
        for words in examples:
            when = parse_request(tuple(words)).when or "today"
            start, end = parse_span(when, reference=today, prefer=Preference.FORWARD)
            assert today <= start <= end <= today + timedelta(days=14), (words, today)


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
