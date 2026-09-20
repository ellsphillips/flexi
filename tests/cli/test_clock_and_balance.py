"""The clock and balance commands, called as functions.

Each command's work is a plain function taking the registry and returning an
exit code, so a test calls it and reads the answer: no CliRunner, no context,
no subprocess, and a failure points at the line that failed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
import time_machine
from sqlalchemy.orm import Session

from flexi.cli import balance as balance_cli
from flexi.cli import clock as clock_cli
from flexi.constants import AbsenceType
from flexi.domain.format import MINUS
from flexi.models.database.db import BankHolidayCache, BankHolidayRefresh
from flexi.services.registry import Services, build_services, invalidate_services
from flexi.services.settings import parse_settings

NOON = date(2026, 6, 10)


def figure(printed: str, label: str) -> timedelta:
    """Return the signed `h:mm` on one line of the balance."""
    line = next(row for row in printed.splitlines() if row.startswith(label))
    reading = line.removeprefix(label).strip()
    sign = -1 if reading.startswith(MINUS) else 1
    hours, minutes = reading.lstrip(f"{MINUS}+").split(":")
    return sign * timedelta(hours=int(hours), minutes=int(minutes))


@pytest.fixture
def services(session: Session) -> Services:
    built = build_services(session)
    built.settings.save_settings(
        parse_settings(
            leave_year_start="04-06",
            working_days="0,1,2,3,4,5,6",
            bank_holiday_division="england-and-wales",
            auto_close_time="18:00",
        )
    )
    # Setup stamps today, and these tests measure across June. `None` is what a
    # database migrated from before the column says: every day counts.
    stored = built.settings.get_settings()
    assert stored is not None
    stored.tracking_since = None
    session.commit()
    return build_services(session)


def test_clocking_in_reports_success(services: Services) -> None:
    assert clock_cli.clock_in(services) == 0


def test_clocking_in_twice_is_a_failure(services: Services) -> None:
    assert clock_cli.clock_in(services) == 0
    assert clock_cli.clock_in(services) == 1


def test_clocking_in_twice_reports_the_session(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    with time_machine.travel(datetime(2026, 6, 10, 9, 0), tick=False):
        clock_cli.clock_in(services)
    with time_machine.travel(datetime(2026, 6, 10, 11, 30), tick=False):
        clock_cli.clock_in(services)

    printed = capsys.readouterr().out
    assert "Already on the clock" in printed
    assert "in at 09:00" in printed
    assert "2:30 on this session" in printed
    assert "hours met at" in printed, "the finish time is the useful half of it"


def test_day_past_its_hours_says_hours_met(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    with time_machine.travel(datetime(2026, 6, 10, 8, 0), tick=False):
        clock_cli.clock_in(services)
    with time_machine.travel(datetime(2026, 6, 10, 18, 0), tick=False):
        clock_cli.clock_in(services)

    printed = capsys.readouterr().out
    assert "hours met" in printed
    assert "hours met at" not in printed


def test_clocking_out_without_clocking_in_fails(
    services: Services,
) -> None:
    assert clock_cli.clock_out(services) == 1


def test_a_refusal_is_said_on_stderr(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    """`flexi clock out >/dev/null || alert` has to leave the reason readable."""
    assert clock_cli.clock_out(services) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Not clocked in" in captured.err


def test_running_session_is_drawn_in_colour(
    services: Services,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`click.echo` stringifies a Rich `Text` to its plain characters."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    with time_machine.travel(datetime(2026, 6, 10, 9, 0), tick=False):
        clock_cli.clock_in(services)
    with time_machine.travel(datetime(2026, 6, 10, 11, 30), tick=False):
        assert clock_cli.clock_in(services) == 1

    printed = capsys.readouterr().out
    assert "Already on the clock" in printed
    assert "\x1b[" in printed, "the rail is drawn in the dashboard palette"


def test_clocking_in_and_out_again(services: Services) -> None:
    assert clock_cli.clock_in(services) == 0
    assert clock_cli.clock_out(services) == 0


def test_the_balance_prints_and_succeeds(services: Services) -> None:
    assert balance_cli.show(services, NOON) == 0


def test_an_empty_log_says_so(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    """A command that prints nothing looks like one that failed to run."""
    assert balance_cli.log(services) == 0
    assert capsys.readouterr().out.strip() == "No adjustments."


def test_adjustment_log_cannot_emit_terminal_controls_or_forged_rows(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    reason = "review\x1b]0;forged\x07\nFAKE ROW\u202e"
    recorded = services.adjustments.record(NOON, timedelta(hours=1), reason)
    assert recorded.success

    assert balance_cli.log(services) == 0

    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert not any(character in output for character in ("\x1b", "\x07", "\u202e"))
    assert "review" in output
    assert services.adjustments.all()[0].reason == reason


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


def test_undoing_a_missing_adjustment_fails(
    services: Services,
) -> None:
    assert balance_cli.undo(services, 9999) == 1


def test_the_balance_agrees_with_its_rows(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sessions are stored to the second and the figures are drawn to the minute."""
    with time_machine.travel(datetime(2026, 6, 10, 9, 0, 0), tick=False):
        clock_cli.clock_in(services)
    with time_machine.travel(datetime(2026, 6, 10, 11, 0, 9), tick=False):
        clock_cli.clock_out(services)
    invalidate_services(services)
    capsys.readouterr()

    assert balance_cli.show(services, NOON) == 0

    printed = capsys.readouterr().out
    worked = figure(printed, "worked")
    expected = figure(printed, "expected")
    assert figure(printed, "balance") == worked - expected


def test_a_balance_for_a_future_day_is_refused(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every future working day would be charged as a full unworked day."""
    with time_machine.travel(datetime(2026, 6, 10, 12, 0), tick=False):
        assert balance_cli.show(services, date(2026, 6, 11)) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "has not happened" in captured.err


def test_the_balance_as_at_today_is_reported(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal is for `>` today, not `>=`: today is the documented default."""
    with time_machine.travel(datetime(2026, 6, 10, 12, 0), tick=False):
        assert balance_cli.show(services, date(2026, 6, 10)) == 0

    assert "balance" in capsys.readouterr().out


def tracking_from(session: Session, when: date) -> Services:
    """Return the same services with a tracking start stamped on them."""
    stored = build_services(session).settings.get_settings()
    assert stored is not None
    stored.tracking_since = when
    session.commit()
    return build_services(session)


def test_a_report_before_tracking_omits_the_note(
    session: Session, services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tracking date after the reported span explains nothing about it."""
    built = tracking_from(session, date(2026, 6, 1))

    assert balance_cli.show(built, date(2026, 5, 20)) == 0
    assert "tracking" not in capsys.readouterr().out


def test_a_report_reaching_the_tracking_date_says_so(
    session: Session, services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    built = tracking_from(session, date(2026, 6, 1))

    assert balance_cli.show(built, NOON) == 0
    assert "tracking     1 Jun 2026 onwards" in capsys.readouterr().out


# What the balance is made of


BANK_HOLIDAY = date(2026, 8, 31)


@pytest.fixture
def stocked(services: Services, session: Session) -> Services:
    """Return the same services with a bank holiday calendar on them.

    `AbsenceService` refuses every booking while the calendar answers `None`,
    so a test that books anything needs at least one cached row.
    """
    session.add_all(
        (
            BankHolidayRefresh(
                division="england-and-wales",
                fetched_at=datetime(2026, 1, 1, 9, 0),
            ),
            BankHolidayCache(
                division="england-and-wales",
                date=BANK_HOLIDAY,
                title="Summer bank holiday",
            ),
        )
    )
    session.commit()
    return build_services(session)


def test_toil_taken_is_shown_on_its_own_line(
    stocked: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    """Folding TOIL into `worked` would leave the day looking unworked."""
    plan = stocked.absence.plan(NOON, NOON, AbsenceType.FLEXI)
    stocked.absence.book_plan(plan)
    invalidate_services(stocked)

    assert balance_cli.show(stocked, NOON) == 0
    assert "toil taken" in capsys.readouterr().out


def test_the_balance_warns_when_there_is_no_calendar(
    services: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    """The balance is the only place a missing calendar shows."""
    balance_cli.show(services, NOON)

    reported = capsys.readouterr().err
    assert "No bank holiday calendar" in reported
    assert "flexi holidays refresh" in reported


def test_the_balance_is_quiet_with_a_calendar(
    stocked: Services, capsys: pytest.CaptureFixture[str]
) -> None:
    balance_cli.show(stocked, NOON)

    assert capsys.readouterr().err == ""
