"""One way to say "a Flexi that has been set up".

The bank holiday cache needs at least one row. `BankHolidayService.titles_between`
answers `None`, not an empty mapping, when the calendar is absent, and
`AbsenceService` refuses to book against `None`. A test that forgets the row
gets "Bank holiday data unavailable; cannot book absence" back from every
booking and can then assert something else and pass.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from flexi.models.database.db import BankHolidayCache, BankHolidayRefresh
from flexi.services.registry import Services, build_services, invalidate_services
from flexi.services.settings import parse_settings

CONTRACTED = timedelta(minutes=444)
"""7:24, the default working day."""

DEFAULT_HOLIDAY = date(2026, 8, 31)
"""Summer bank holiday, England & Wales: far from most test weeks, and present
so the calendar answers `False` and not `None`."""

type Configured = Callable[..., Services]


@pytest.fixture
def configure(session: Session) -> Configured:
    """Set Flexi up and hand back a registry built against it."""

    def build(
        *,
        leave_year_start: str = "10-20",
        working_days: str = "0,1,2,3,4",
        division: str = "england-and-wales",
        auto_close_time: str = "18:00",
        entitlement: tuple[int, float] | None = None,
        tracking_since: date | None = None,
        holidays: tuple[tuple[date, str], ...] = (
            (DEFAULT_HOLIDAY, "Summer bank holiday"),
        ),
    ) -> Services:
        built = build_services(session)
        built.settings.save_settings(
            parse_settings(
                leave_year_start=leave_year_start,
                working_days=working_days,
                bank_holiday_division=division,
                auto_close_time=auto_close_time,
            )
        )
        # Set after saving, which stamps it with today. `None` is the migrated
        # database's answer: every day in the leave year counts. A test about
        # the gap before setup passes a date.
        stored = built.settings.get_settings()
        assert stored is not None
        stored.tracking_since = tracking_since
        fetched_at = datetime(2026, 1, 1, 9, 0, tzinfo=UTC).replace(tzinfo=None)
        session.add(BankHolidayRefresh(division=division, fetched_at=fetched_at))
        for when, title in holidays:
            session.add(
                BankHolidayCache(
                    division=division,
                    date=when,
                    title=title,
                )
            )
        session.commit()
        if entitlement is not None:
            built.settings.save_entitlement(*entitlement)
        return built

    return build


@pytest.fixture
def services(configure: Configured) -> Services:
    """The common case: set up, one bank holiday cached, 25 days for 2025."""
    return configure(entitlement=(2025, 25.0))


def work(services: Services, when: date, hours: float, *, start_hour: int = 9) -> None:
    """Clock a session in and out on a date.

    Through the clock and not by inserting rows, so the void-if-too-short rule
    applies here as it does in the application.
    """
    start = datetime.combine(when, datetime.min.time(), tzinfo=UTC).replace(
        hour=start_hour
    )
    services.clock.clock_in(now=start)
    services.clock.clock_out(now=start + timedelta(hours=hours))
    invalidate_services(services)
