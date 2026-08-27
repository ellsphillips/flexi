"""The routing in `__main__`, which is the only part of it that is not routing.

Every command here is four lines: refuse before setup, open the database, hand
the registry to a plain function in `flexi.cli.*`, exit with the code it
returns. Those functions are asserted on directly in the files beside this one,
because that is far cheaper than a `CliRunner` and points at the line that
failed rather than at Click.

What is left over cannot be reached any other way. The guard that refuses a
command on an unconfigured machine, the flag that opens the sample data instead
of somebody's records, and the fork between "already set up" and "ask the five
questions" all live in the wiring, and the reset arm of that fork is the one
thing in Flexi that loses data.

The application is stood in for throughout. `FlexiApp.__init__` builds an engine and
`run` wants a terminal, and neither is what is under test here: what matters is
*which* database it was pointed at, whether the splash was earned, and whether
it was opened at all.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import time_machine
from click.testing import CliRunner

import flexi.__main__ as main
from flexi.__main__ import cli
from flexi.cli import init as init_cli
from flexi.cli import ui
from flexi.locations import backups_directory, database_file
from flexi.models.database.db import AbsenceDay, BankHolidayCache, BankHolidayRefresh
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.migrate import run_migrations
from flexi.services.registry import build_services
from flexi.services.settings import parse_settings

MONDAY = datetime(2026, 8, 10, 12, 0)
"""The clock every test in this file runs against.

A leave command reads `wallclock.today()` inside the Click callback, so `friday`
means whatever the machine says. Holding the clock still is what lets the
expectation be written down.
"""

BANK_HOLIDAY = date(2026, 8, 31)
"""Summer bank holiday, England & Wales.

Present so the calendar answers `False` rather than `None`. `AbsenceService`
refuses every booking while the cache is bare, and the suite blocks the network,
so a `home` without this row turns every leave test into an assertion about a
refusal nobody meant to write.
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

    Migrated through Alembic rather than `create_all`: every command migrates on
    the way in, and a schema built behind Alembic's back carries no stamp, which
    is exactly the state `is_initialised` is written to answer False for.
    """
    db = database_file()
    db.parent.mkdir(parents=True, exist_ok=True)
    run_migrations(db)
    set_up(db)
    return db


# -- standing in for the application -----------------------------------------


class _Opened:
    """Stands in for the application, which needs a terminal to be worth building.

    Holds the three things `__main__` decides about it: which database it was
    pointed at, whether the splash animation was earned, and whether it was told
    to land on the settings screen.
    """

    def __init__(self, db_path: Path | None, on_run: OnRun | None) -> None:
        self.db_path = db_path
        self.show_splash = False
        self.open_settings = False
        self.ran = False
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

    Patched at `flexi.app.FlexiApp`, not on `__main__`: the name is imported inside
    `launch` and `run_demo` so that `flexi --version` does not load six
    Textual screens, which means there is nothing bound here to replace.
    """
    opened: list[_Opened] = []

    def building(*, db_path: Path | None = None) -> _Opened:
        app = _Opened(db_path, on_run)
        opened.append(app)
        return app

    monkeypatch.setattr("flexi.app.FlexiApp", building)
    return opened


def answering_the_questions(app: _Opened) -> None:
    """What the setup screen does when somebody actually fills it in.

    `ask_the_questions` asks the database whether setup finished, never the
    form, so this has to write the row rather than merely return.
    """
    set_up(app.db_path or database_file())


def choosing(monkeypatch: pytest.MonkeyPatch, choice: init_cli.Choice | None) -> None:
    """Stand at the `flexi init` menu and pick something, or escape."""
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)

    def picking(
        question: str,
        options: Sequence[ui.Option[init_cli.Choice]],
    ) -> ui.Option[init_cli.Choice] | None:
        if choice is None:
            return None
        return next(option for option in options if option.value == choice)

    monkeypatch.setattr("flexi.cli.ui.choose", picking)


# -- the sample data ---------------------------------------------------------


def test_the_demo_never_opens_the_records_on_this_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sample data is seeded somewhere throwaway, never here.

    `--demo` is what a new user is shown and what the screenshots are cut from,
    so it has to seed a database before it opens one. Seeding the real one to
    draw a picture would wipe a year of somebody's work.
    """
    instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["--demo"])

    assert result.exit_code == 0, result.output
    assert not database_file().exists(), "the demo must not touch the real database"


def test_the_demo_is_thrown_away_when_it_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing survives the demo.

    Six weeks of invented records left behind on disk are indistinguishable
    from six weeks of real ones the next time somebody goes looking.
    """
    opened = instead_of_the_application(monkeypatch)

    CliRunner().invoke(cli, ["--demo"])

    assert opened[0].db_path is not None
    assert not opened[0].db_path.exists()


def test_the_demo_opens_a_working_life_rather_than_an_empty_week(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty demo is a worse advertisement than no demo.

    The seed is read while the application is up, because that is the only
    moment it exists: the temporary directory goes as `run_demo` returns.
    """
    counted: list[int] = []

    def read_it(app: _Opened) -> None:
        with sqlite3.connect(f"file:{app.db_path}?mode=ro", uri=True) as sample:
            counted.append(
                sample.execute("SELECT count(*) FROM work_sessions").fetchone()[0]
            )

    instead_of_the_application(monkeypatch, read_it)

    CliRunner().invoke(cli, ["--demo"])

    assert counted, "the application was never opened"
    assert counted[0] > 0


def test_the_demo_flag_does_not_take_a_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`flexi --demo clock in` is refused rather than resolved.

    It reads as "clock in to the sample data", and clocking in to the real
    records instead is the wrong half of that to guess at silently.
    """
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["--demo", "clock", "in"])

    assert result.exit_code == 2
    assert "does not take a command" in result.output
    assert opened == [], "nothing is seeded and nothing is opened"


# -- bare `flexi` ------------------------------------------------------------


def test_bare_flexi_on_a_new_machine_sets_itself_up_rather_than_refusing() -> None:
    """Bare `flexi` on a fresh machine prepares itself and asks the questions.

    The guard exists to stop clock, leave and balance inventing answers from
    defaults nobody chose. It is not there to make the application decline to
    open on the very machine that needs setting up.
    """
    result = CliRunner().invoke(cli, [])

    assert database_file().is_file(), "the database was created and migrated"
    assert "not set up on this machine yet" not in result.output, (
        "bare `flexi` must not be turned away by the guard on clock and leave"
    )
    assert result.exit_code == 1
    assert "setup needs answering" in result.output
    assert "flexi init" in result.output


def test_bare_flexi_carries_straight_on_once_the_questions_are_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """That is what the person asked for.

    Typing `flexi` means "open Flexi", and answering five questions on the way
    in does not change what was asked for. `flexi init` is the one that stops
    and reports, because setting up is all it was asked to do.
    """
    instead_of_the_application(monkeypatch, answering_the_questions)
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert "Flexi is set up" not in result.output, "nothing to report; it just opens"


def test_the_first_run_earns_the_splash(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = instead_of_the_application(monkeypatch, answering_the_questions)
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)

    CliRunner().invoke(cli, [])

    assert [app.show_splash for app in opened] == [True]


def test_closing_the_setup_form_without_answering_is_not_treated_as_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A form that was closed rather than filled in leaves the guard up.

    Every getter in `settings` substitutes a default for a missing row, so a
    half-answered machine answers every question confidently and wrongly:
    `flexi balance show` on one reports a deficit of a thousand hours against a
    leave year nobody chose.
    """
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)
    instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 1
    assert "Setup was not completed" in result.output


def test_bare_flexi_on_a_set_up_machine_just_opens_it(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No questions, and no animation: the splash is for a first run."""
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert [(app.ran, app.show_splash) for app in opened] == [(True, False)]


# -- the guard ---------------------------------------------------------------


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
def test_a_command_before_setup_is_refused_and_told_what_to_run(
    command: list[str],
) -> None:
    """Refused before anything is opened, and told which command fixes it.

    A migrated-but-unconfigured database answers every question confidently and
    wrongly, so the refusal has to come before the database is created rather
    than after it.
    """
    result = CliRunner().invoke(cli, command)

    assert result.exit_code == 1
    assert "not set up on this machine yet" in result.output
    assert "flexi init" in result.output
    assert not database_file().exists(), "refusing must not leave a database behind"


def test_help_is_reachable_on_a_machine_with_no_database() -> None:
    """The guard is applied per command rather than to the group for this.

    On the group it ran before Click had resolved the subcommand, which refused
    `flexi init` on the very machine that needed it and turned `flexi clock
    --help` into an error message about setup.
    """
    result = CliRunner().invoke(cli, ["clock", "--help"])

    assert result.exit_code == 0, result.output
    assert "Clock in or out" in result.output


# -- the commands, wired up --------------------------------------------------


def test_clocking_in_from_the_command_line(home: Path) -> None:
    result = CliRunner().invoke(cli, ["clock", "in"])

    assert result.exit_code == 0, result.output
    assert "Clocked in" in result.output


def test_refreshing_the_calendar_offline_fails_rather_than_reporting_nothing(
    home: Path,
) -> None:
    """Offline is the ordinary case on a train, and it is not a silent one.

    The exit code is what a cron entry reads. Flexi keeps working; it simply
    has no calendar until it can be reached.
    """
    result = CliRunner().invoke(cli, ["holidays", "refresh"])

    assert result.exit_code == 1
    assert "Could not reach GOV.UK" in result.output


def test_leave_shows_the_plan_and_writes_nothing_on_a_dry_run(home: Path) -> None:
    """Nothing is written until the plan has been shown and agreed.

    A dry run is how somebody checks which days `friday` actually meant before
    a week of leave goes on the record.
    """
    result = CliRunner().invoke(cli, ["leave", "annual", "friday", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Booking annual leave" in result.output
    assert "14 Aug" in result.output
    assert booked_days(home) == []


def test_leave_books_the_days_it_showed(home: Path) -> None:
    result = CliRunner().invoke(cli, ["leave", "annual", "friday", "--yes"])

    assert result.exit_code == 0, result.output
    assert booked_days(home) == [date(2026, 8, 14)]


def test_other_leave_carries_the_note_it_was_given(home: Path) -> None:
    """`--note` is the whole point of `other`.

    A day off the books with no reason attached is a day nobody can account for
    a year later, which is why the kind is refused without one.
    """
    result = CliRunner().invoke(
        cli, ["leave", "other", "friday", "--note", "jury service", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert notes(home) == ["jury service"]


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


# -- `flexi init` on a machine with nothing on it ----------------------------


def test_init_with_nobody_there_to_answer_stops_and_says_where_it_got_to() -> None:
    """The setup form is a full screen, and a pipe is not a terminal.

    Reporting how far it got beats leaving somebody staring at a command that
    appeared to do nothing at all.
    """
    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 1
    assert "The database is ready at" in result.output
    assert "Run `flexi init` from a terminal to finish." in result.output


def test_init_finishes_and_says_where_the_records_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`flexi init` stops once the questions are answered.

    It does not carry on into the application the way bare `flexi` does: the
    person asked to set Flexi up, not to use it.
    """
    opened = instead_of_the_application(monkeypatch, answering_the_questions)
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert f"Flexi is set up. Its records are at {database_file()}" in result.output
    assert [app.show_splash for app in opened] == [True]


def test_a_database_the_migration_finishes_is_not_asked_the_questions_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup can complete without anybody answering anything.

    A database carrying answers but no Alembic stamp reads as not set up until
    it has been migrated, so `flexi init` asks again on the other side of the
    migration instead of pressing on. Pressing on would put the setup form over
    a leave year, an allowance and a region that are already there.
    """

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


# -- `flexi init` on a machine that already has records ----------------------


def test_init_with_nobody_there_reports_what_is_on_the_machine_and_stops(
    home: Path,
) -> None:
    """`yes | flexi init` gets a description and an exit, not a menu.

    There is deliberately no way to erase Flexi's records without a person
    present to type the word, and no flag that stands in for one.
    """
    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert str(home) in result.output
    assert "from a terminal to change or reset them" in result.output
    assert home.is_file()


def test_open_from_the_menu_opens_the_records_as_they_are(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    choosing(monkeypatch, init_cli.Choice.OPEN)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert [(app.ran, app.open_settings) for app in opened] == [(True, False)]
    assert home.is_file()


def test_change_settings_from_the_menu_lands_on_the_settings_screen(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The menu offered to change the settings, so it has to arrive at them.

    Otherwise the answer to "I chose the wrong leave year" is to go and find
    the screen by hand, having just been offered it.
    """
    choosing(monkeypatch, init_cli.Choice.SETTINGS)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert [app.open_settings for app in opened] == [True]


def test_escaping_the_menu_leaves_the_machine_exactly_as_it_was(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escape has to mean escape on the one menu that can erase records.

    Not "open anyway", and certainly not "carry on to the next question".
    """
    choosing(monkeypatch, None)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert opened == []
    assert home.is_file()


def test_declining_the_last_gate_erases_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Choosing "Start again" is not the agreement; typing the word is.

    Somebody who does not type it has to end up back exactly where they
    started, with the records and the menu they arrived with.
    """
    choosing(monkeypatch, init_cli.Choice.RESET)
    monkeypatch.setattr("flexi.cli.ui.type_the_word", lambda *_a, **_k: False)
    opened = instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert "Nothing was erased." in result.output
    assert home.is_file()
    assert opened == [], "nor is the setup form opened over records still there"


def test_starting_again_keeps_a_snapshot_and_then_asks_the_questions(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one command in Flexi that loses data.

    A snapshot is taken first and the person is told where it went, because
    nothing else brings the records back. The five questions follow immediately:
    a machine left erased and unconfigured is not a state anybody chose.
    """
    choosing(monkeypatch, init_cli.Choice.RESET)
    monkeypatch.setattr("flexi.cli.ui.type_the_word", lambda *_a, **_k: True)
    opened = instead_of_the_application(monkeypatch, answering_the_questions)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert "Erased. Snapshot kept at" in result.output
    assert [app.show_splash for app in opened] == [True], "a first run all over again"
    assert list(backups_directory().glob("*.bak")), "the only way back"


def test_starting_again_forgets_that_this_machine_was_ever_set_up(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The remembered answer has to go with the records.

    `is_initialised` memoises the affirmative and nothing invalidates it, so
    without the forget this command would erase everything, reopen the setup
    form, and then congratulate somebody on a setup that no longer exists.
    """
    choosing(monkeypatch, init_cli.Choice.RESET)
    monkeypatch.setattr("flexi.cli.ui.type_the_word", lambda *_a, **_k: True)
    instead_of_the_application(monkeypatch)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 1
    assert "Setup was not completed" in result.output


def test_erasing_a_database_that_is_not_there_promises_no_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`reset` answers None when there was no file to copy.

    The rail must not then close with "Snapshot kept at None", pointing at a
    backup nobody has on the one path where the safety net is the whole point.
    """
    main.erase(tmp_path / "absent.db")

    assert "Snapshot" not in capsys.readouterr().err


def test_the_leave_examples_are_listed_one_per_line() -> None:
    r"""Click's no-rewrap marker is a backspace character, not a backslash.

    The docstring holding the five examples was a raw string, so `\b` was two
    characters Click does not recognise and the examples were rewrapped into a
    paragraph with a stray `\b` at the front of it -- five commands run
    together into prose, in the help text of the command most likely to be read
    before it is used.

    It became raw to satisfy ruff's D301, which asks for a raw docstring
    wherever one contains a backslash. That rule and this feature want opposite
    things, and the rule is silenced there with its reason. Nothing about that
    is visible from either side, so it is asserted from the outside.
    """
    output = CliRunner().invoke(cli, ["leave", "--help"]).output

    assert "\\b" not in output
    assert "\b" not in output
    for example in ("flexi leave annual friday", "flexi leave sick today pm"):
        assert f"\n  {example}\n" in output, output
