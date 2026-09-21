"""Refreshing the bank holiday calendar from the command line."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from flexi.cli import holidays as holidays_cli
from flexi.constants import DEFAULT_DIVISION
from flexi.services.bank_holidays import GOVUK_URL
from flexi.services.registry import Services, build_services
from flexi.services.settings import parse_settings

SCOTLAND = "scotland"

PAYLOAD: dict[str, Any] = {
    "england-and-wales": {
        "division": "england-and-wales",
        "events": [
            {"title": "Summer bank holiday", "date": "2026-08-31"},
            {"title": "Christmas Day", "date": "2026-12-25"},
        ],
    },
    "scotland": {"division": "scotland", "events": []},
}
"""The shape GOV.UK publishes: two English dates and an empty Scottish list."""


@pytest.fixture
def answering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow a GOV.UK reply, over the suite-wide block on outbound requests."""
    monkeypatch.setattr(
        "httpx.Client.send",
        lambda *_args, **_kwargs: httpx.Response(
            200, json=PAYLOAD, request=httpx.Request("GET", GOVUK_URL)
        ),
    )


def configured(session: Session, division: str = "england-and-wales") -> Services:
    """Return a registry for a machine whose region has been chosen.

    `build_services` reads the division once, so the registry is rebuilt after
    the settings row is saved.
    """
    built = build_services(session)
    built.settings.save_settings(
        parse_settings(
            leave_year_start="04-06",
            working_days="Mon-Fri",
            bank_holiday_division=division,
            auto_close_time="18:00",
        )
    )
    return build_services(session)


def test_refresh_caches_the_calendar(session: Session, answering: None) -> None:
    services = configured(session)

    assert holidays_cli.run(services) == 0
    assert services.bank_holidays.get_dates() == {
        date(2026, 8, 31),
        date(2026, 12, 25),
    }


def test_refresh_reports_count_and_region(
    session: Session, answering: None, capsys: pytest.CaptureFixture[str]
) -> None:
    holidays_cli.run(configured(session))

    assert "2 bank holidays cached for England & Wales." in capsys.readouterr().out


def test_empty_division_reports_zero(
    session: Session, answering: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """`get_dates` answers None, not an empty set, when the cache is bare."""
    assert holidays_cli.run(configured(session, SCOTLAND)) == 0
    assert "0 bank holidays cached for Scotland." in capsys.readouterr().out


def test_unreachable_govuk_fails_loudly(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    services = configured(session)

    assert holidays_cli.run(services) == 1

    reported = capsys.readouterr().err
    assert "Could not reach GOV.UK for England & Wales." in reported
    assert "Flexi keeps working" in reported
    assert services.bank_holidays.get_dates() is None


def test_failed_refresh_keeps_the_cached_calendar(
    session: Session, answering: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale calendar still answers for the year it holds, so it is kept."""
    services = configured(session)
    assert holidays_cli.run(services) == 0, "the first refresh fills the cache"
    capsys.readouterr()

    def refused(*_args: object, **_kwargs: object) -> None:
        msg = "GOV.UK is unreachable this time"
        raise httpx.ConnectError(msg)

    with pytest.MonkeyPatch.context() as offline:
        offline.setattr("httpx.Client.send", refused)
        assert holidays_cli.run(services) == 1, "a cron entry reads this"

    reported = capsys.readouterr().err
    assert "Could not reach GOV.UK for England & Wales." in reported
    assert "already cached is unchanged" in reported
    assert "will be missing" not in reported
    assert services.bank_holidays.get_dates() == {
        date(2026, 8, 31),
        date(2026, 12, 25),
    }


def test_missing_settings_reports_the_default_region(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert holidays_cli.run(build_services(session)) == 1
    assert DEFAULT_DIVISION.label in capsys.readouterr().err
