"""The balance commands, driven the way somebody would drive them."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from flexi.__main__ import cli
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.migrate import run_migrations
from flexi.services.registry import Services

YESTERDAY = date.today() - timedelta(days=1)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A database somewhere harmless, with a short day recorded on it."""
    db = tmp_path / "db.db"
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
    start = datetime.combine(YESTERDAY, datetime.min.time(), tzinfo=timezone.utc)
    services.clock.clock_in(now=start.replace(hour=9))
    services.clock.clock_out(now=start.replace(hour=11))
    session.close()
    engine.dispose()

    monkeypatch.setattr("flexi.locations.database_file", lambda: db)
    monkeypatch.setattr("flexi.models.database.migrate.database_file", lambda: db)
    monkeypatch.setattr("flexi.models.database.app.database_file", lambda: db)
    return db


def test_show_reports_the_balance(home: Path) -> None:
    """It prints what the figure is made of, not just the figure."""
    result = CliRunner().invoke(cli, ["balance", "show"])
    assert result.exit_code == 0, result.output
    assert "worked" in result.output
    assert "balance" in result.output


def balance_of(runner: CliRunner, when: date | None = None) -> str:
    args = ["balance", "show"]
    if when is not None:
        args += ["--as-of", when.isoformat()]
    return runner.invoke(cli, args).output.split("balance")[-1].strip()


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
    """log names the row, undo removes it, and the balance returns."""
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
        assert len(Services.build(session).clock.get_sessions_for_date(YESTERDAY)) == 1
    finally:
        session.close()
