"""The balance commands, driven from the command line."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import time_machine
from click.testing import CliRunner

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
    """Log names the row, undo removes it, and the balance returns."""
    runner = CliRunner()
    runner.invoke(cli, ["balance", "zero", "--yes"])

    log = runner.invoke(cli, ["balance", "log"])
    assert "opening balance" in log.output
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
