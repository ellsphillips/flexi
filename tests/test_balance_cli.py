"""The balance commands, driven the way somebody would drive them."""

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
from flexi.services.registry import Services
from tests.conftest import sessions_on

NOON = datetime(2026, 6, 10, 12, 0)
"""The clock these tests run against.

`YESTERDAY` used to be `wallclock.today() - timedelta(days=1)`, evaluated when
the module was imported. That reads the real clock once, before any test runs:
the module cannot be exercised under a frozen clock at all, and a suite that
starts before midnight and reaches this file after it compares two different
days. Holding the clock still makes both go away.
"""

YESTERDAY = (NOON - timedelta(days=1)).date()


@pytest.fixture(autouse=True)
def _at_noon() -> Iterator[None]:
    with time_machine.travel(NOON, tick=False):
        yield


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A database somewhere harmless, with a short day recorded on it.

    The path comes from `database_file()` under the throwaway XDG home the
    root conftest sets, rather than from monkeypatching the binding in every
    module that imported it. Doing it the second way meant remembering all of
    them, and this fixture patched three of the four: `flexi.services.setup`
    was missed, so the guard on every balance command read the developer's own
    machine instead of the temporary one.
    """
    db = database_file()
    db.parent.mkdir(parents=True, exist_ok=True)
    # Through alembic, not create_all: the CLI migrates on every invocation, and
    # a schema built behind its back has no version stamped on it.
    run_migrations(db)
    engine = create_db_engine(db)
    session = get_session(engine)
    services = Services.build(session)
    services.settings.save_settings(
        leave_year_start=YESTERDAY.strftime("%m-%d"),
        working_days="0,1,2,3,4,5,6",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    start = datetime.combine(YESTERDAY, datetime.min.time(), tzinfo=UTC)
    services.clock.clock_in(now=start.replace(hour=9))
    services.clock.clock_out(now=start.replace(hour=11))
    session.close()
    engine.dispose()

    return db


def test_show_reports_the_balance(home: Path) -> None:
    """It prints what the figure is made of, not just the figure."""
    result = CliRunner().invoke(cli, ["balance", "show"])
    assert result.exit_code == 0, result.output
    assert "worked" in result.output
    assert "balance" in result.output


def balance_of(runner: CliRunner, when: date | None = None) -> str:
    """The figure on the `balance` line.

    Read off the line rather than by splitting the whole output on the word,
    which broke the moment anything was printed after it -- and something is:
    `balance show` now says when there is no bank holiday calendar, because
    that is the line the missing days are missing from.
    """
    args = ["balance", "show"]
    if when is not None:
        args += ["--as-of", when.isoformat()]
    output = runner.invoke(cli, args).output
    line = next(row for row in output.splitlines() if row.startswith("balance"))
    return line.removeprefix("balance").strip()


def test_zero_settles_it(home: Path) -> None:
    """It draws the line where it said it would."""
    runner = CliRunner()
    assert balance_of(runner, YESTERDAY) != "0:00"

    result = runner.invoke(cli, ["balance", "zero", "--yes"])
    assert result.exit_code == 0, result.output
    assert "adjusted" in result.output.lower()

    assert balance_of(runner, YESTERDAY) == "0:00"


def test_zeroing_leaves_today_alone(home: Path) -> None:
    """Today is not over.

    Absorbing its contracted hours before they have been worked would leave the
    evening looking like unearned overtime, so the line is drawn at the end of
    yesterday and today counts normally.
    """
    runner = CliRunner()
    runner.invoke(cli, ["balance", "zero", "--yes"])
    assert balance_of(runner, YESTERDAY) == "0:00"
    assert balance_of(runner) == "−7:24"


def test_zero_asks_before_it_writes(home: Path) -> None:
    """Declining leaves the records exactly as they were."""
    result = CliRunner().invoke(cli, ["balance", "zero"], input="n\n")
    assert result.exit_code == 0
    assert "Left alone" in result.output
    assert "No adjustments" in CliRunner().invoke(cli, ["balance", "log"]).output


def test_zero_is_refused_twice(home: Path) -> None:
    """The second one would be a row that does nothing."""
    runner = CliRunner()
    runner.invoke(cli, ["balance", "zero", "--yes"])
    again = runner.invoke(cli, ["balance", "zero", "--yes"])
    assert again.exit_code == 1
    assert "already zero" in again.output


def test_the_line_can_be_taken_back(home: Path) -> None:
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


def test_the_work_records_are_untouched(home: Path) -> None:
    """Settling is a correction, never a deletion."""
    CliRunner().invoke(cli, ["balance", "zero", "--yes"])
    session = get_session(create_db_engine(home))
    try:
        assert len(sessions_on(session, YESTERDAY)) == 1
    finally:
        session.close()


def test_as_of_reads_the_dates_the_rest_of_the_command_line_reads(home: Path) -> None:
    """One grammar across one command line.

    `--as-of` was a `click.DateTime` accepting only `%Y-%m-%d`, so
    `flexi leave annual friday` worked and `flexi balance show --as-of friday`
    was a usage error — and the refusal named `%Y-%m-%d` rather than the forms
    Flexi actually understands.
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
