"""The balance commands, driven from the command line."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import time_machine
from click.testing import CliRunner, Result

from flexi.__main__ import cli
from flexi.locations import database_file
from flexi.models.database.engine import create_db_engine, get_session
from flexi.models.database.migrate import run_migrations
from flexi.services.registry import build_services
from flexi.services.settings import parse_settings
from tests.conftest import session_at, sessions_on

NOON = datetime(2026, 6, 10, 12, 0)
"""The clock these tests run against.

`YESTERDAY` is derived from it and not from `wallclock.today()`: a module-level
read of the real clock happens before any test can freeze it, and a suite that
crosses midnight would then compare two different days.
"""

YESTERDAY = (NOON - timedelta(days=1)).date()


@pytest.fixture(autouse=True)
def _at_noon() -> Iterator[None]:
    with time_machine.travel(NOON, tick=False):
        yield


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A database somewhere harmless, with a short day recorded on it.

    The path comes from `database_file()` under the throwaway XDG home the root
    conftest sets, so every module that imported the binding reads that one.
    """
    db = database_file()
    db.parent.mkdir(parents=True, exist_ok=True)
    # Through alembic, not create_all: the CLI migrates on every invocation, and
    # a schema built behind its back has no version stamped on it.
    run_migrations(db)
    engine = create_db_engine(db)
    session = get_session(engine)
    services = build_services(session)
    services.settings.save_settings(
        parse_settings(
            leave_year_start=YESTERDAY.strftime("%m-%d"),
            working_days="0,1,2,3,4,5,6",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    start = datetime.combine(YESTERDAY, datetime.min.time(), tzinfo=UTC)
    services.clock.clock_in(now=start.replace(hour=9))
    services.clock.clock_out(now=start.replace(hour=11))
    session.close()
    engine.dispose()

    return db


def test_show_reports_the_balance(home: Path) -> None:
    """The figure arrives with what it is made of."""
    result = CliRunner().invoke(cli, ["balance", "show"])
    assert result.exit_code == 0, result.output
    assert "worked" in result.output
    assert "balance" in result.output


def balance_of(runner: CliRunner, when: date | None = None) -> str:
    """The figure on the `balance` line, which is not the last line printed."""
    args = ["balance", "show"]
    if when is not None:
        args += ["--as-of", when.isoformat()]
    output = runner.invoke(cli, args).output
    line = next(row for row in output.splitlines() if row.startswith("balance"))
    return line.removeprefix("balance").strip()


def test_zero_settles_it(home: Path) -> None:
    runner = CliRunner()
    assert balance_of(runner, YESTERDAY) != "0:00"

    result = runner.invoke(cli, ["balance", "zero", "--yes"])
    assert result.exit_code == 0, result.output
    assert "adjusted" in result.output.lower()

    assert balance_of(runner, YESTERDAY) == "0:00"


def test_zeroing_leaves_today_alone(home: Path) -> None:
    """The line is drawn at the end of yesterday, so today counts normally.

    Absorbing today's contracted hours before they are worked would leave the
    evening looking like unearned overtime.
    """
    runner = CliRunner()
    runner.invoke(cli, ["balance", "zero", "--yes"])
    assert balance_of(runner, YESTERDAY) == "0:00"
    assert balance_of(runner) == "−7:24"


def test_zero_asks_before_it_writes(home: Path) -> None:
    """Declining exits 1, as a declined booking does, and writes nothing.

    `flexi balance zero && flexi balance show` has to tell the two apart.
    """
    result = CliRunner().invoke(cli, ["balance", "zero"], input="n\n")
    assert result.exit_code == 1
    assert "Left alone" in result.output
    assert "No adjustments" in CliRunner().invoke(cli, ["balance", "log"]).output


def test_settlement_question_is_asked_on_stderr(home: Path) -> None:
    """`flexi balance zero > log` must not send the question into the file."""
    result = CliRunner().invoke(cli, ["balance", "zero"], input="n\n")

    assert "Settle it to zero?" in result.stderr
    assert "Settle it to zero?" not in result.stdout
    assert "balance as at" in result.stdout, "the standing is the output"


def test_unfinished_day_is_refused_with_no_figure(
    home: Path,
) -> None:
    """The standing it would be sized from is a projection.

    Every day between now and the date counts as zero hours worked, so printing
    it first would offer several hundred hours as a reading.
    """
    result = CliRunner().invoke(cli, ["balance", "zero", "--as-of", "today", "--yes"])

    assert result.exit_code == 1
    assert "has not finished" in result.output
    assert "balance as at" not in result.stdout


def test_non_utf8_reason_is_refused_before_the_write(
    home: Path,
) -> None:
    """The reason is stored, so a lone surrogate would reach SQLite."""
    result = CliRunner().invoke(
        cli, ["balance", "zero", "--reason", "caf\udce9", "--yes"]
    )

    assert result.exit_code == 2
    assert "not valid UTF-8" in result.output
    assert "No adjustments" in CliRunner().invoke(cli, ["balance", "log"]).output


def test_zero_is_refused_twice(home: Path) -> None:
    """The second one would be a row that does nothing."""
    runner = CliRunner()
    runner.invoke(cli, ["balance", "zero", "--yes"])
    again = runner.invoke(cli, ["balance", "zero", "--yes"])
    assert again.exit_code == 1
    assert "already zero" in again.output


def test_settlement_can_be_taken_back(home: Path) -> None:
    """Log names the row, undo removes it, and the balance returns.

    Named a settlement, so it reads apart from an opening balance brought in
    with `flexi balance adjust`.
    """
    runner = CliRunner()
    runner.invoke(cli, ["balance", "zero", "--yes"])

    log = runner.invoke(cli, ["balance", "log"])
    assert "settled" in log.output
    assert "opening balance" not in log.output
    row_id = log.output.split()[0]

    undone = runner.invoke(cli, ["balance", "undo", row_id])
    assert undone.exit_code == 0
    assert "removed" in undone.output

    assert balance_of(runner, YESTERDAY) != "0:00"


def test_work_records_are_untouched(home: Path) -> None:
    """Settling is a correction, never a deletion."""
    result = CliRunner().invoke(cli, ["balance", "zero", "--yes"])
    assert result.exit_code == 0, result.output
    assert "adjusted" in result.output.lower()

    with session_at(home) as session:
        assert len(sessions_on(session, YESTERDAY)) == 1


def test_as_of_reads_the_same_dates_as_other_commands(home: Path) -> None:
    """One date grammar across the whole command line.

    `--as-of` takes the words `flexi leave annual friday` takes, and its refusal
    names the forms Flexi understands.
    """
    runner = CliRunner()

    assert balance_of(runner, YESTERDAY) == _balance_on(runner, "yesterday")

    refused = runner.invoke(cli, ["balance", "show", "--as-of", "whenever"])
    assert refused.exit_code != 0
    assert "12 Jun" in refused.output, "the refusal should name what it accepts"


def _balance_on(runner: CliRunner, typed: str) -> str:
    output = runner.invoke(cli, ["balance", "show", "--as-of", typed]).output
    line = next(row for row in output.splitlines() if row.startswith("balance"))
    return line.removeprefix("balance").strip()


# Adjusting


@pytest.fixture
def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal on both ends, so the question is asked and answered."""
    monkeypatch.setattr("flexi.cli.ui.interactive", lambda: True)


def adjust(runner: CliRunner, *args: str, answer: str | None = None) -> Result:
    return runner.invoke(cli, ["balance", "adjust", *args], input=answer)


def logged(runner: CliRunner) -> str:
    return runner.invoke(cli, ["balance", "log"]).output


def test_adjust_moves_the_balance(home: Path) -> None:
    runner = CliRunner()
    assert balance_of(runner) == "−12:48"

    result = adjust(runner, "+5:30", "--reason", "Brought forward", "--yes")

    assert result.exit_code == 0, result.output
    assert "adjusted by +5:30" in result.stdout
    assert balance_of(runner) == "−7:18"


@pytest.mark.parametrize("typed", ["-1:30", "\N{MINUS SIGN}1:30"])
def test_a_deficit_is_an_amount_not_an_option(home: Path, typed: str) -> None:
    """Click reads `-1:30` as an option `-1` unless told otherwise.

    U+2212 is the minus Flexi prints, so a figure pasted back has it.
    """
    runner = CliRunner()

    result = adjust(runner, typed, "--reason", "Left early", "--yes")

    assert result.exit_code == 0, result.output
    assert "\N{MINUS SIGN}1:30  Left early" in logged(runner)


@pytest.mark.parametrize(
    ("typed", "said"), [("5", "use H:MM"), ("5:3", "use H:MM"), ("0:00", "nothing")]
)
def test_unreadable_or_zero_amount_is_a_usage_error(
    home: Path, typed: str, said: str
) -> None:
    runner = CliRunner()

    result = adjust(runner, typed, "--reason", "Brought forward", "--yes")

    assert result.exit_code == 2
    assert said in result.stderr
    assert "No adjustments" in logged(runner)


@pytest.mark.parametrize("reason", [None, "   "])
def test_adjust_needs_a_reason(home: Path, reason: str | None) -> None:
    """Every row is read back in `flexi balance log`, so each says why."""
    runner = CliRunner()
    given = () if reason is None else ("--reason", reason)

    result = adjust(runner, "+5:30", *given, "--yes")

    assert result.exit_code == 2
    assert "--reason" in result.stderr
    assert "No adjustments" in logged(runner)


@pytest.mark.usefixtures("at_a_terminal")
def test_adjust_shows_the_balance_it_makes_and_asks(home: Path) -> None:
    """Declining exits 1 and writes nothing, as declining a settlement does."""
    runner = CliRunner()

    result = adjust(runner, "+5:30", "--reason", "Brought forward", answer="n\n")

    assert result.exit_code == 1
    assert "+5:30 on Wed 10 Jun 2026" in result.stdout
    assert "Brought forward" in result.stdout
    assert "−12:48 → −7:18" in result.stdout
    assert "Record it?" in result.stderr, "the question is not the output"
    assert "Nothing was recorded" in result.stderr
    assert "No adjustments" in logged(runner)


@pytest.mark.usefixtures("at_a_terminal")
def test_adjust_records_what_was_agreed(home: Path) -> None:
    runner = CliRunner()

    result = adjust(runner, "+5:30", "--reason", "Brought forward", answer="y\n")

    assert result.exit_code == 0, result.output
    assert "+5:30  Brought forward" in logged(runner)


def test_adjust_without_a_terminal_refuses_instead_of_asking(home: Path) -> None:
    """A pipe is not someone answering, and a pipe left open would never answer."""
    runner = CliRunner()

    result = adjust(runner, "+5:30", "--reason", "Brought forward", answer="y\n")

    assert result.exit_code == 1
    assert "--yes" in result.stderr
    assert "No adjustments" in logged(runner)


def test_adjust_on_an_earlier_day_reads_the_shared_dates(home: Path) -> None:
    """`--on` takes the words every other date option takes."""
    runner = CliRunner()

    result = adjust(runner, "-1:30", "--on", "yesterday", "--reason", "x", "--yes")

    assert result.exit_code == 0, result.output
    assert f"{YESTERDAY:%Y-%m-%d}" in logged(runner)


@pytest.mark.parametrize(
    ("when", "said"),
    [("tomorrow", "has not happened"), ("2026-06-08", "Tue 9 Jun 2026")],
)
def test_adjust_outside_the_leave_year_so_far_is_refused(
    home: Path, when: str, said: str
) -> None:
    """Refused before the plan, which would offer a figure that never counts."""
    runner = CliRunner()

    result = adjust(runner, "+1:00", "--on", when, "--reason", "x", "--yes")

    assert result.exit_code == 1
    assert said in result.stderr
    assert "→" not in result.stdout
    assert "No adjustments" in logged(runner)


def test_adjust_behind_a_settlement_is_refused(home: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(cli, ["balance", "zero", "--yes"]).exit_code == 0
    line = logged(runner).split()[0]

    result = adjust(runner, "+1:00", "--on", "yesterday", "--reason", "x", "--yes")

    assert result.exit_code == 1
    assert f"flexi balance undo {line}" in result.stderr
    assert balance_of(runner, YESTERDAY) == "0:00"


def test_a_balance_brought_in_can_be_corrected_the_next_day(home: Path) -> None:
    """The README's examples a day apart: an opening balance is no settlement."""
    runner = CliRunner()
    with time_machine.travel(NOON - timedelta(days=1), tick=False):
        brought = adjust(runner, "+5:30", "--reason", "Brought forward", "--yes")
    assert brought.exit_code == 0, brought.output

    result = adjust(
        runner, "-0:45", "--on", "yesterday", "--reason", "Long lunch", "--yes"
    )

    assert result.exit_code == 0, result.output
    assert balance_of(runner) == "\N{MINUS SIGN}8:03"


def test_adjustment_is_logged_and_can_be_taken_back(home: Path) -> None:
    runner = CliRunner()
    adjust(runner, "+5:30", "--reason", "Brought forward", "--yes")

    log = logged(runner)
    assert "2026-06-10" in log
    assert "+5:30  Brought forward" in log

    undone = runner.invoke(cli, ["balance", "undo", log.split()[0]])

    assert undone.exit_code == 0
    assert "No adjustments" in logged(runner)
    assert balance_of(runner) == "−12:48"
