"""Refreshing the bank holiday calendar from the command line.

An empty cache is not a quiet failure. Every leave booking is refused against
it, and every bank holiday is counted as a working day nobody worked -- so the
one command that fills it has to say plainly whether it managed, and for which
region. `flexi.cli.holidays.run` is a plain function taking the registry and
returning an exit code, so a script can read the answer and a test can too.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session

from flexi.cli import holidays as holidays_cli
from flexi.constants import DEFAULT_DIVISION
from flexi.services.registry import Services

SCOTLAND = "scotland"

PAYLOAD: dict[str, Any] = {
    "england-and-wales": {
        "division": "england-and-wales",
        "events": [
            {"title": "Summer bank holiday", "date": "2026-08-31"},
            {"title": "Christmas Day", "date": "2026-12-25"},
        ],
    }
}
"""The shape GOV.UK publishes, cut down to two dates."""


class _Answered:
    """What `httpx.Client.get` hands back when the request goes through."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture
def answering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let GOV.UK reply, over the top of the suite's refusal to make requests."""
    monkeypatch.setattr(
        "httpx.Client.get", lambda *_args, **_kwargs: _Answered(PAYLOAD)
    )


def configured(session: Session, division: str = "england-and-wales") -> Services:
    """A registry for a machine whose region has been chosen.

    Rebuilt after saving: `Services.build` reads the division once, and a
    registry made before the settings row exists holds the default.
    """
    built = Services.build(session)
    built.settings.save_settings(
        leave_year_start="04-06",
        working_days="Mon-Fri",
        bank_holiday_division=division,
        auto_close_time="18:00",
    )
    return Services.build(session)


def test_a_refresh_that_reaches_govuk_caches_the_calendar(
    session: Session, answering: None
) -> None:
    """The point of the command: dates in the cache afterwards."""
    services = configured(session)

    assert holidays_cli.run(services) == 0
    assert services.bank_holidays.get_dates() == {
        date(2026, 8, 31),
        date(2026, 12, 25),
    }


def test_a_refresh_says_how_many_it_cached_and_for_where(
    session: Session, answering: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """It names the region as well as the count.

    Two calendars differ by a handful of days, and picking the wrong region is
    silent until somebody is refused leave on a day their office is shut.
    """
    holidays_cli.run(configured(session))

    assert "2 bank holidays cached for England & Wales." in capsys.readouterr().out


def test_a_division_govuk_publishes_nothing_for_reports_none_rather_than_failing(
    session: Session, answering: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """`get_dates` answers None, not an empty set, when the cache is bare.

    The fetch succeeded; there is simply nothing under that key. Counting the
    answer without allowing for None is a `TypeError` on the last line of a
    command that had already done its job.
    """
    assert holidays_cli.run(configured(session, SCOTLAND)) == 0
    assert "0 bank holidays cached for Scotland." in capsys.readouterr().out


def test_a_refresh_that_cannot_reach_govuk_fails_without_hiding_it(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    """Offline is the ordinary case on a train, and it is not a silent one.

    The exit code is what a cron entry reads. Flexi keeps working; it just has
    no calendar until the fetch can go through.
    """
    services = configured(session)

    assert holidays_cli.run(services) == 1

    reported = capsys.readouterr().err
    assert "Could not reach GOV.UK for England & Wales." in reported
    assert "Flexi keeps working" in reported
    assert services.bank_holidays.get_dates() is None


def test_a_machine_with_no_settings_row_is_named_by_the_default_region(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    """Setup has not been answered, so there is no chosen region to report.

    Saying nothing at all would leave somebody reading "could not reach GOV.UK
    for " with a blank where the answer should be.
    """
    assert holidays_cli.run(Services.build(session)) == 1
    assert DEFAULT_DIVISION.label in capsys.readouterr().err
