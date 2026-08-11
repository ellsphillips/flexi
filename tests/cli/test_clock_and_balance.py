"""The clock and balance commands, called as functions.

`cli/leave.py` was already this shape, and `tests/cli/test_leave_command.py`
exploits it: the work is a plain function taking the registry and returning an
exit code, so a test calls it and reads the answer. No CliRunner, no context, no
subprocess, and a failure points at the line that failed rather than at Click.

These two lived in `__main__` among the routing, so the only way to reach them
was through the runner -- which is why neither had any coverage at all.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import time_machine

from flexi.cli import balance as balance_cli
from flexi.cli import clock as clock_cli
from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import Base
from flexi.services.registry import Services

NOON = date(2026, 6, 10)


@pytest.fixture
def services(tmp_path: Path) -> Services:
    engine = create_db_engine(tmp_path / "f.db")
    Base.metadata.create_all(engine)
    session = get_session(engine)
    built = Services.build(session)
    built.settings.save_settings(
        leave_year_start="04-06",
        working_days="0,1,2,3,4,5,6",
        bank_holiday_division="england-and-wales",
        auto_close_time="18:00",
    )
    return Services.build(session)


def test_clocking_in_reports_success(services: Services) -> None:
    assert clock_cli.clock_in(services) == 0


def test_clocking_in_twice_is_a_failure(services: Services) -> None:
    """The exit code is what a script reads, and it was never checked."""
    assert clock_cli.clock_in(services) == 0
    assert clock_cli.clock_in(services) == 1


def test_clocking_out_without_clocking_in_is_a_failure(
    services: Services,
) -> None:
    assert clock_cli.clock_out(services) == 1


def test_clocking_in_and_out_again(services: Services) -> None:
    assert clock_cli.clock_in(services) == 0
    assert clock_cli.clock_out(services) == 0


def test_the_balance_prints_and_succeeds(services: Services) -> None:
    assert balance_cli.show(services, NOON) == 0


def test_the_log_is_empty_until_something_is_settled(services: Services) -> None:
    assert balance_cli.log(services) == 0


def test_settling_and_taking_it_back(services: Services) -> None:
    with time_machine.travel(NOON, tick=False):
        clock_cli.clock_in(services)
        clock_cli.clock_out(services)

        assert (
            balance_cli.zero(services, NOON - timedelta(days=1), assume_yes=True) == 0
        )
        rows = services.adjustments.all()
        assert len(rows) == 1

        assert balance_cli.undo(services, rows[0].id) == 0
        assert services.adjustments.all() == []


def test_undoing_something_that_is_not_there_is_a_failure(
    services: Services,
) -> None:
    assert balance_cli.undo(services, 9999) == 1
