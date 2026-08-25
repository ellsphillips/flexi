from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from flexi.constants import Division
from flexi.models.database.db import BankHolidayCache

GOVUK_URL = "https://www.gov.uk/bank-holidays.json"
CACHE_MAX_AGE = timedelta(days=7)
REQUEST_TIMEOUT = 5.0


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

    def _cache_is_fresh(self) -> bool:
        stmt = (
            select(BankHolidayCache.fetched_at)
            .where(BankHolidayCache.division == self.division)
            .limit(1)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return False
        age = datetime.now(tz=UTC) - row.replace(tzinfo=UTC)
        return age < CACHE_MAX_AGE

    # ---- fetch ----

    def fetch_and_cache(self) -> bool:
        """Fetch from GOV.UK and replace the DB cache. Returns True on success."""
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                response = client.get(GOVUK_URL)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError, OSError):
            return False

        division = self.division
        events = data.get(division, {}).get("events", [])
        now = datetime.now(tz=UTC).replace(tzinfo=None)

        # Clear old cache for this division
        self._session.execute(
            delete(BankHolidayCache).where(BankHolidayCache.division == division)
        )

        for event in events:
            try:
                when = date.fromisoformat(event["date"])
                title = event.get("title", "")
            except (KeyError, ValueError):
                continue
            self._session.add(
                BankHolidayCache(
                    division=division,
                    date=when,
                    title=title,
                    fetched_at=now,
                )
            )
        self._session.commit()
        return True

    def ensure_cache(self) -> bool:
        """Refresh the cache if stale. True if there is data to read afterwards.

        Nothing in the application called this. The only route to a populated
        cache was a command-palette entry, so a person who only ever used the
        command line could not reach one -- and an empty cache is not a quiet
        state. `book_range` refuses every day of it, and the ledger counts every
        bank holiday as a working day nobody worked.
        """
        if self._cache_is_fresh():
            return True
        return self.fetch_and_cache()

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

    def is_bank_holiday(self, day: date) -> bool | None:
        """Check if a date is a bank holiday. Returns None if data unavailable."""
        found = self.titles_between(day, day)
        return None if found is None else day in found

    def get_title(self, day: date) -> str | None:
        """Return bank holiday title for a date, or None."""
        return (self.titles_between(day, day) or {}).get(day)

    def get_dates(self) -> set[date] | None:
        """Return all cached bank holiday dates, or None if unavailable."""
        if not self.is_available():
            return None
        stmt = select(BankHolidayCache.date).where(
            BankHolidayCache.division == self.division
        )
        return set(self._session.execute(stmt).scalars())
