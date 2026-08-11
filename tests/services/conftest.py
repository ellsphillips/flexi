"""One way to say "a Flexi that has been set up".

Fifteen files in this directory built that phrase by hand, two of them byte for
byte, and the invariant that makes them work was re-explained in three different
comments: there has to be at least one row in the bank holiday cache, because
`BankHolidayService.is_bank_holiday` answers `None` — not `False` — when the
calendar is absent, and `AbsenceService` refuses to book against `None` rather
than silently treating an unknown day as workable.

A test that forgets the cache row does not fail loudly. It gets "Bank holiday
data unavailable; cannot book absence" back from every booking and then asserts
something else, which is how a test can pass while exercising nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from flexi.models.database.db import BankHolidayCache
from flexi.services.registry import Services

CONTRACTED = timedelta(minutes=444)
"""7:24, the default working day. Named because a test asserting `444` is a test
whose reader has to go and look."""

DEFAULT_HOLIDAY = date(2026, 8, 31)
"""Summer bank holiday, England & Wales. Far enough from most test weeks to be
inert, and present so the calendar answers `False` rather than `None`."""

type Configured = Callable[..., Services]


@pytest.fixture
def configure(session: Session) -> Configured:
    """Set Flexi up and hand back a registry built against it.

    Rebuilt after saving, because `Services.build` reads the division once and
    a registry made before the settings row exists holds the default.
    """

    def build(
        *,
        leave_year_start: str = "10-20",
        working_days: str = "0,1,2,3,4",
        division: str = "england-and-wales",
        auto_close_time: str = "18:00",
        entitlement: tuple[int, float] | None = None,
        holidays: tuple[tuple[date, str], ...] = (
            (DEFAULT_HOLIDAY, "Summer bank holiday"),
        ),
    ) -> Services:
        built = Services.build(session)
        built.settings.save_settings(
            leave_year_start=leave_year_start,
            working_days=working_days,
            bank_holiday_division=division,
            auto_close_time=auto_close_time,
        )
        for when, title in holidays:
            session.add(
                BankHolidayCache(
                    division=division,
                    date=when,
                    title=title,
                    fetched_at=datetime(2026, 1, 1, 9, 0, tzinfo=UTC).replace(
                        tzinfo=None
                    ),
                )
            )
        session.commit()
        rebuilt = Services.build(session)
        if entitlement is not None:
            rebuilt.settings.save_entitlement(*entitlement)
        return rebuilt

    return build


@pytest.fixture
def services(configure: Configured) -> Services:
    """The common case: set up, one bank holiday cached, 25 days for 2025."""
    return configure(entitlement=(2025, 25.0))


def work(services: Services, when: date, hours: float, *, start_hour: int = 9) -> None:
    """A session on a date, clocked in and out like any other.

    Written through the clock rather than by inserting rows, so a test that
    depends on work having happened depends on the same code the application
    runs — including the void-if-too-short rule.
    """
    start = datetime.combine(when, datetime.min.time(), tzinfo=UTC).replace(
        hour=start_hour
    )
    services.clock.clock_in(now=start)
    services.clock.clock_out(now=start + timedelta(hours=hours))
    services.invalidate()
