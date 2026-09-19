from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Thread

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import Division
from flexi.domain.format import printable
from flexi.models.database.db import (
    BankHolidayAttempt,
    BankHolidayCache,
    BankHolidayRefresh,
)
from flexi.services.transactions import atomic

__all__ = (
    "CACHE_MAX_AGE",
    "GOVUK_URL",
    "REQUEST_TIMEOUT",
    "RETRY_AFTER",
    "BankHolidayFetcher",
    "BankHolidayService",
    "ParsedBankHoliday",
    "fetch_bank_holiday_index",
    "parse_bank_holidays",
)

GOVUK_URL = "https://www.gov.uk/bank-holidays.json"
CACHE_MAX_AGE = timedelta(days=7)
REQUEST_TIMEOUT = 5.0

_FETCH_BUDGET = 2 * REQUEST_TIMEOUT
"""Wall-clock bound on the whole fetch, from the calling thread.

``httpx`` bounds connecting, reading, writing and pooling separately, and none
of those covers name resolution: ``getaddrinfo`` has no timeout of its own, and
an unreachable resolver blocks for about thirty seconds on macOS.
"""

RETRY_AFTER = timedelta(hours=1)
"""How long an empty calendar waits before GOV.UK is asked for it again.

Every command opens the database, and opening it fills an absent calendar, so
offline this is the whole fetch budget once per command. An explicit `flexi
holidays refresh` is never held back by it.
"""

type BankHolidayFetcher = Callable[[], object | None]
"""A source of an untrusted bank-holiday index, or ``None`` on failure."""


@dataclass(frozen=True, slots=True)
class ParsedBankHoliday:
    """One validated event from the GOV.UK bank-holiday index."""

    date: date
    title: str


def parse_bank_holidays(
    payload: object, division: Division
) -> tuple[ParsedBankHoliday, ...] | None:
    """Validate one division of the GOV.UK response without side effects.

    ``None`` means the response cannot be trusted; an empty tuple is a valid,
    empty ``events`` list. Validation is all or nothing, because replacing a
    complete cached calendar with a partial response turns every omitted
    holiday into a working day. Titles are stripped of the characters a
    terminal obeys here, so that every reader of the cache gets the same text.
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
    seen_dates: set[date] = set()
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
        if when in seen_dates:
            return None
        seen_dates.add(when)
        parsed.append(ParsedBankHoliday(date=when, title=printable(raw_title)))

    return tuple(parsed)


def fetch_bank_holiday_index() -> object | None:
    """Fetch the GOV.UK index, returning ``None`` for an unusable response.

    The local ``httpx`` import keeps it off every command that only reads the
    cache; validating the payload belongs to :func:`parse_bank_holidays`.
    """
    import httpx

    fetched: list[object] = []

    def request() -> None:
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                response = client.get(GOVUK_URL)
                response.raise_for_status()
                fetched.append(response.json())
        # A broken environment makes `httpx.Client` raise before any request:
        # a socks proxy URL without the socks extra raises `ImportError`, a bad
        # proxy port `httpx.InvalidURL`, a missing `SSL_CERT_FILE` `OSError`.
        except Exception:  # noqa: BLE001 - documented to return None for any failure
            return

    # Daemon, so a resolver still waiting cannot hold the process open past the
    # budget: `ThreadPoolExecutor` joins its workers at interpreter shutdown.
    worker = Thread(target=request, daemon=True)
    worker.start()
    worker.join(_FETCH_BUDGET)
    return fetched[0] if fetched else None


class BankHolidayService:
    """Fetch, cache (in DB), and validate GOV.UK bank holidays."""

    def __init__(
        self,
        session: Session,
        division: Callable[[], Division],
        fetcher: BankHolidayFetcher = fetch_bank_holiday_index,
    ) -> None:
        """Store the session and a callable that answers the current division.

        The division is a question, not a value: settings can change under a
        screen that is already mounted, so it is asked afresh on every use.
        """
        self._session = session
        self._division = division
        self._fetcher = fetcher

    @property
    def division(self) -> Division:
        """The division whose calendar this reads, asked afresh each time."""
        return self._division()

    # ---- cache freshness ----

    def is_fresh(self) -> bool:
        """True when the cache was fetched inside :data:`CACHE_MAX_AGE`."""
        fetched_at = self.last_refresh()
        if fetched_at is None:
            return False
        age = wallclock.utc_now() - fetched_at
        return age < CACHE_MAX_AGE

    def last_refresh(self, division: Division | None = None) -> datetime | None:
        """Return the last successful complete fetch as an aware UTC moment.

        A refresh has its own row and is not inferred from event rows, so a
        valid response with no holidays is still a calendar. An explicit
        division holds one division stable across a compound query.
        """
        selected = self.division if division is None else division
        stmt = select(BankHolidayRefresh.fetched_at).where(
            BankHolidayRefresh.division == selected
        )
        fetched_at = self._session.execute(stmt).scalar_one_or_none()
        if fetched_at is None:
            return None
        return fetched_at.replace(tzinfo=UTC)

    # ---- fetch ----

    def fetch_and_cache(self) -> bool:
        """Fetch from GOV.UK and replace the DB cache. Returns True on success.

        A fetch that comes back with nothing is written down, so the next
        command to find an empty calendar can decline to wait for the same
        answer.
        """
        data = self._fetcher()
        if self.cache_payload(data):
            return True
        self._remember_attempt()
        return False

    def cache_payload(self, payload: object) -> bool:
        """Validate and atomically cache a supplied bank-holiday index.

        The persistence half of :meth:`fetch_and_cache`, exposed so a host can
        keep the network call outside its database-owning context: in the
        Textual application a worker thread fetches the untrusted payload and
        the message loop hands it here. ``False`` means validation failed, a
        ``None`` payload included, and leaves the cached calendar intact.
        """
        division = self.division
        events = parse_bank_holidays(payload, division)
        if events is None:
            return False
        now = wallclock.utc_now().replace(tzinfo=None)

        with atomic(self._session):
            self._session.execute(
                delete(BankHolidayCache).where(BankHolidayCache.division == division)
            )
            self._session.execute(
                delete(BankHolidayAttempt).where(
                    BankHolidayAttempt.division == division
                )
            )
            self._session.execute(
                insert(BankHolidayRefresh)
                .values(division=division.value, fetched_at=now)
                .on_conflict_do_update(
                    index_elements=(BankHolidayRefresh.division,),
                    set_={"fetched_at": now},
                )
            )

            for event in events:
                self._session.add(
                    BankHolidayCache(
                        division=division,
                        date=event.date,
                        title=event.title,
                    )
                )
        return True

    def fill_if_empty(self) -> bool:
        """Fetch only when there is nothing at all. True if data is available.

        A stale cache still answers every question correctly for the year it
        holds, so it is left alone; an empty one answers nothing. Empty and
        asked for inside :data:`RETRY_AFTER` is the third case, where the wait
        buys the same silence twice.
        """
        if self.is_available():
            return True
        if self.asked_recently():
            return False
        return self.fetch_and_cache()

    def asked_recently(self) -> bool:
        """Whether this division was asked for inside :data:`RETRY_AFTER`."""
        stmt = select(BankHolidayAttempt.attempted_at).where(
            BankHolidayAttempt.division == self.division
        )
        attempted_at = self._session.execute(stmt).scalar_one_or_none()
        if attempted_at is None:
            return False
        return wallclock.utc_now() - attempted_at.replace(tzinfo=UTC) < RETRY_AFTER

    def _remember_attempt(self) -> None:
        """Record that GOV.UK was asked and had nothing to give."""
        now = wallclock.utc_now().replace(tzinfo=None)
        with atomic(self._session):
            self._session.execute(
                insert(BankHolidayAttempt)
                .values(division=self.division.value, attempted_at=now)
                .on_conflict_do_update(
                    index_elements=(BankHolidayAttempt.division,),
                    set_={"attempted_at": now},
                )
            )

    def titles_between(self, start: date, end: date) -> dict[date, str] | None:
        """Return the holidays in a span, or None when there is no calendar.

        An empty mapping means the span holds no holidays; ``None`` means
        nothing is cached for this division at all.
        """
        division = self.division
        if self.last_refresh(division) is None:
            return None
        stmt = select(BankHolidayCache.date, BankHolidayCache.title).where(
            BankHolidayCache.division == division,
            BankHolidayCache.date >= start,
            BankHolidayCache.date <= end,
        )
        return {row.date: row.title for row in self._session.execute(stmt)}

    # ---- validation helpers ----

    def is_available(self) -> bool:
        """Return whether a complete calendar is cached for this division."""
        return self.last_refresh() is not None

    def holiday_on(self, day: date) -> str | None:
        """Return what this date is a bank holiday for, or ``None``.

        ``None`` covers both "no holiday" and "no calendar". A caller that must
        tell them apart uses :meth:`titles_between`.
        """
        return (self.titles_between(day, day) or {}).get(day)

    def get_dates(self) -> set[date] | None:
        """Every cached bank holiday, or None when there is no calendar at all."""
        division = self.division
        if self.last_refresh(division) is None:
            return None
        stmt = select(BankHolidayCache.date).where(
            BankHolidayCache.division == division
        )
        return set(self._session.execute(stmt).scalars())
