from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import Division
from flexi.models.database.db import BankHolidayCache

GOVUK_URL = "https://www.gov.uk/bank-holidays.json"
CACHE_MAX_AGE = timedelta(days=7)
REQUEST_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class ParsedBankHoliday:
    """One validated event from the GOV.UK bank-holiday index."""

    date: date
    title: str


def parse_bank_holidays(
    payload: object, division: Division
) -> tuple[ParsedBankHoliday, ...] | None:
    """Validate one division of the GOV.UK response without side effects.

    ``None`` means the response cannot be trusted. Validation is deliberately
    atomic: replacing a complete cached calendar with a partial response would
    silently turn every omitted holiday into a working day. An empty tuple is a
    valid, explicitly empty ``events`` list and remains distinct from failure.

    A missing title is accepted as an empty label because the date is the fact
    the ledger needs. A title that is present but is not text is malformed.
    """
    if not isinstance(payload, Mapping):
        return None

    division_payload: object = payload.get(division.value)
    if not isinstance(division_payload, Mapping):
        return None

    raw_events: object = division_payload.get("events")
    if not isinstance(raw_events, list):
        return None

    parsed: list[ParsedBankHoliday] = []
    for raw_event in raw_events:
        event: object = raw_event
        if not isinstance(event, Mapping):
            return None

        raw_date: object = event.get("date")
        raw_title: object = event.get("title", "")
        if not isinstance(raw_date, str) or not isinstance(raw_title, str):
            return None
        try:
            when = date.fromisoformat(raw_date)
        except ValueError:
            return None
        parsed.append(ParsedBankHoliday(date=when, title=raw_title))

    return tuple(parsed)


class BankHolidayService:
    """Fetch, cache (in DB), and validate GOV.UK bank holidays."""

    def __init__(self, session: Session, division: Callable[[], Division]) -> None:
        """Takes a way to find the division out, rather than the division.

        Required either way: it once defaulted to England & Wales, and every
        caller that forgot to pass one got the English calendar silently.
        `ClockService` was one of them, so the bank-holiday guard was inverted
        for Scotland and Northern Ireland: blocked on an English holiday,
        allowed on their own.

        A *question* rather than an answer because the answer changes. Held as
        a value, it went stale the moment somebody chose a different division in
        settings, and the application worked around that by rebuilding the whole
        registry -- which left every screen already on screen reading the
        registry it had replaced.
        """
        self._session = session
        self._division = division

    @property
    def division(self) -> Division:
        """Whose calendar this reads, asked afresh each time."""
        return self._division()

    # ---- cache freshness ----

    def is_fresh(self) -> bool:
        """True when the cache was fetched recently enough to trust.

        A stale cache still answers every question correctly for the year it
        holds, so this is what keeps a GOV.UK timeout off the launch path six
        days out of seven.
        """
        stmt = (
            select(BankHolidayCache.fetched_at)
            .where(BankHolidayCache.division == self.division)
            .limit(1)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return False
        age = wallclock.utc_now() - row.replace(tzinfo=UTC)
        return age < CACHE_MAX_AGE

    # ---- fetch ----

    def fetch_and_cache(self) -> bool:
        """Fetch from GOV.UK and replace the DB cache. Returns True on success.

        `httpx` is imported here rather than at module scope. It costs sixty
        milliseconds to import and this is the only method that needs it, so
        every `flexi clock in` -- which opens this service to *read* the cache
        and never touches the network -- was paying for an HTTP client.
        """
        import httpx

        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                response = client.get(GOVUK_URL)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError, OSError):
            return False

        division = self.division
        events = parse_bank_holidays(data, division)
        if events is None:
            return False
        now = wallclock.utc_now().replace(tzinfo=None)

        # Clear old cache for this division
        self._session.execute(
            delete(BankHolidayCache).where(BankHolidayCache.division == division)
        )

        for event in events:
            self._session.add(
                BankHolidayCache(
                    division=division,
                    date=event.date,
                    title=event.title,
                    fetched_at=now,
                )
            )
        self._session.commit()
        return True

    def fill_if_empty(self) -> bool:
        """Fetch only when there is nothing at all. True if data is available.

        The difference between empty and stale matters on the command line. A
        stale cache still answers every question correctly for the year it
        holds, so paying a network round trip for it would put a timeout in
        front of `flexi clock in` once a week. An empty one answers nothing.
        """
        if self.is_available():
            return True
        return self.fetch_and_cache()

    def titles_between(self, start: date, end: date) -> dict[date, str] | None:
        """The holidays in a span, or None when there is no calendar at all.

        Returning an empty mapping for both cases is what let a fresh install
        book a full day's deficit against every bank holiday without saying so.
        """
        division = self.division
        if not self._has_any(division):
            return None
        stmt = select(BankHolidayCache.date, BankHolidayCache.title).where(
            BankHolidayCache.division == division,
            BankHolidayCache.date >= start,
            BankHolidayCache.date <= end,
        )
        return {row.date: row.title for row in self._session.execute(stmt)}

    # ---- validation helpers ----

    def is_available(self) -> bool:
        """Return True if any cached data exists for this division."""
        return self._has_any(self.division)

    def _has_any(self, division: Division) -> bool:
        stmt = (
            select(BankHolidayCache.id)
            .where(BankHolidayCache.division == division)
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none() is not None

    def holiday_on(self, day: date) -> str | None:
        """What this date is a bank holiday for, or ``None``.

        One name where there were three for the same question at one date:
        `is_bank_holiday` answered `bool | None`, so its one caller had to write
        `is True`; `get_title` answered the same thing without the calendar
        check; and `titles_between(day, day)` answered it correctly.

        "No calendar" and "not a holiday" are both `None` here, deliberately.
        The caller that needs to tell them apart is `AbsenceService`, which
        refuses to book against an unknown calendar rather than guessing, and it
        asks `titles_between` for a whole span and reads `has_calendar` off the
        result -- which is the shape that keeps the distinction.
        """
        return (self.titles_between(day, day) or {}).get(day)

    def get_dates(self) -> set[date] | None:
        """Every cached bank holiday, or None when there is no calendar at all."""
        division = self.division
        if not self._has_any(division):
            return None
        stmt = select(BankHolidayCache.date).where(
            BankHolidayCache.division == division
        )
        return set(self._session.execute(stmt).scalars())
