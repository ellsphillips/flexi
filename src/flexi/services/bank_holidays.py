from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from flexi.models.database.db import BankHolidayCache

GOVUK_URL = "https://www.gov.uk/bank-holidays.json"
CACHE_MAX_AGE = timedelta(days=7)
REQUEST_TIMEOUT = 5.0


class BankHolidayService:
    """Fetch, cache (in DB), and validate GOV.UK bank holidays."""

    def __init__(self, session: Session, division: str = "england-and-wales") -> None:
        self._session = session
        self._division = division

    # ---- cache freshness ----

    def _cache_is_fresh(self) -> bool:
        stmt = (
            select(BankHolidayCache.fetched_at)
            .where(BankHolidayCache.division == self._division)
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

        division_data = data.get(self._division, {})
        events = division_data.get("events", [])
        now = datetime.now(tz=UTC).replace(tzinfo=None)

        # Clear old cache for this division
        self._session.execute(
            delete(BankHolidayCache).where(BankHolidayCache.division == self._division)
        )

        for ev in events:
            try:
                d = date.fromisoformat(ev["date"])
                title = ev.get("title", "")
            except (KeyError, ValueError):
                continue
            self._session.add(
                BankHolidayCache(
                    division=self._division,
                    date=d,
                    title=title,
                    fetched_at=now,
                )
            )
        self._session.commit()
        return True

    def ensure_cache(self) -> bool:
        """Refresh cache if stale. Returns True if cache is available."""
        if self._cache_is_fresh():
            return True
        return self.fetch_and_cache()

    # ---- validation helpers ----

    def is_available(self) -> bool:
        """Return True if any cached data exists for this division."""
        stmt = (
            select(BankHolidayCache.id)
            .where(BankHolidayCache.division == self._division)
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none() is not None

    def is_bank_holiday(self, d: date) -> bool | None:
        """Check if a date is a bank holiday. Returns None if data unavailable."""
        if not self.is_available():
            return None
        stmt = select(BankHolidayCache).where(
            BankHolidayCache.division == self._division,
            BankHolidayCache.date == d,
        )
        return self._session.execute(stmt).scalar_one_or_none() is not None

    def get_title(self, d: date) -> str | None:
        """Return bank holiday title for a date, or None."""
        stmt = select(BankHolidayCache.title).where(
            BankHolidayCache.division == self._division,
            BankHolidayCache.date == d,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def get_dates(self) -> set[date] | None:
        """Return all cached bank holiday dates, or None if unavailable."""
        if not self.is_available():
            return None
        stmt = select(BankHolidayCache.date).where(
            BankHolidayCache.division == self._division
        )
        return set(self._session.execute(stmt).scalars())
