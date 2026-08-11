"""The bank holiday cache: hits, stale refreshes, and an index that is down.

Absence booking refuses outright when holiday data is unavailable, so the
difference between "no holidays" and "could not tell" has to survive the cache.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TypedDict
from unittest.mock import patch

import httpx
from sqlalchemy.orm import Session

from flexi.models.database.db import BankHolidayCache
from flexi.services.bank_holidays import BankHolidayService


class Event(TypedDict):
    title: str
    date: str


class Division(TypedDict):
    division: str
    events: list[Event]


SAMPLE_RESPONSE: dict[str, Division] = {
    "england-and-wales": {
        "division": "england-and-wales",
        "events": [
            {"title": "New Year's Day", "date": "2026-01-01"},
            {"title": "Good Friday", "date": "2026-04-03"},
            {"title": "Christmas Day", "date": "2026-12-25"},
        ],
    },
    "scotland": {
        "division": "scotland",
        "events": [
            {"title": "New Year's Day", "date": "2026-01-01"},
            {"title": "St Andrew's Day", "date": "2026-11-30"},
        ],
    },
}


def _seed_cache(session: Session, division: str = "england-and-wales") -> None:
    """Insert sample bank holidays directly into the cache table."""
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    known = SAMPLE_RESPONSE.get(division)
    for ev in known["events"] if known else ():
        session.add(
            BankHolidayCache(
                division=division,
                date=date.fromisoformat(ev["date"]),
                title=ev["title"],
                fetched_at=now,
            )
        )
    session.commit()


# ---------- cache hit ----------


class TestCacheHit:
    def test_dates_from_fresh_cache(self, session: Session) -> None:
        _seed_cache(session)
        svc = BankHolidayService(session, "england-and-wales")
        dates = svc.get_dates()
        assert dates is not None
        assert date(2026, 1, 1) in dates
        assert date(2026, 12, 25) in dates
        assert len(dates) == 3

    def test_is_bank_holiday(self, session: Session) -> None:
        _seed_cache(session)
        svc = BankHolidayService(session, "england-and-wales")
        assert svc.is_bank_holiday(date(2026, 1, 1)) is True
        assert svc.is_bank_holiday(date(2026, 6, 15)) is False


# ---------- stale refresh ----------


class TestStaleRefresh:
    def test_stale_cache_triggers_refresh(self, session: Session) -> None:
        # Insert old entries
        old = datetime.now(tz=UTC) - timedelta(days=10)
        session.add(
            BankHolidayCache(
                division="england-and-wales",
                date=date(2026, 1, 1),
                title="Old",
                fetched_at=old.replace(tzinfo=None),
            )
        )
        session.commit()

        svc = BankHolidayService(session, "england-and-wales")
        assert svc._cache_is_fresh() is False


# ---------- fetch failure ----------


class TestFetchFailure:
    def test_unavailable_when_no_cache(self, session: Session) -> None:
        svc = BankHolidayService(session, "england-and-wales")
        assert svc.is_available() is False
        assert svc.get_dates() is None
        assert svc.is_bank_holiday(date(2026, 1, 1)) is None

    def test_fetch_failure_returns_false(self, session: Session) -> None:
        svc = BankHolidayService(session, "england-and-wales")
        with patch(
            "flexi.services.bank_holidays.httpx.Client",
            side_effect=httpx.ConnectError("network"),
        ):
            assert svc.fetch_and_cache() is False


# ---------- division changes ----------


class TestDivisionChanges:
    def test_different_division_different_dates(self, session: Session) -> None:
        _seed_cache(session, "england-and-wales")
        _seed_cache(session, "scotland")

        ew = BankHolidayService(session, "england-and-wales")
        sc = BankHolidayService(session, "scotland")

        assert ew.is_bank_holiday(date(2026, 12, 25)) is True
        assert sc.is_bank_holiday(date(2026, 12, 25)) is False
        assert sc.is_bank_holiday(date(2026, 11, 30)) is True


# ---------- title lookup ----------


class TestTitleLookup:
    def test_get_title(self, session: Session) -> None:
        _seed_cache(session)
        svc = BankHolidayService(session, "england-and-wales")
        assert svc.get_title(date(2026, 12, 25)) == "Christmas Day"
        assert svc.get_title(date(2026, 6, 15)) is None


class TestFillingTheCache:
    """Nothing in the application ever filled it.

    `ensure_cache` had no caller in `src/`; the only route to a populated cache
    was a Textual command-palette entry, so a person who used Flexi from the
    command line could not reach one. An empty cache is not a quiet state: every
    leave booking is refused, and every bank holiday is counted as a working day
    nobody worked.
    """

    def test_it_fetches_when_there_is_nothing_at_all(self, session: Session) -> None:
        svc = BankHolidayService(session, "england-and-wales")
        assert svc.is_available() is False

        # The suite refuses outbound requests, so this is the offline first run.
        assert svc.fill_if_empty() is False

    def test_it_does_not_fetch_when_there_is_already_a_calendar(
        self, session: Session
    ) -> None:
        """Stale is not empty.

        A stale calendar answers correctly for the year it holds, so refreshing
        it on the command line would put a network timeout in front of `flexi
        clock in` once a week.
        """
        session.add(
            BankHolidayCache(
                division="england-and-wales",
                date=date(2020, 1, 1),
                title="ancient",
                fetched_at=datetime(2020, 1, 1),
            )
        )
        session.commit()
        svc = BankHolidayService(session, "england-and-wales")

        assert svc._cache_is_fresh() is False, "the fixture should be stale"
        assert svc.fill_if_empty() is True, "and stale is good enough to keep"


class TestTellingEmptyFromAbsent:
    def test_no_calendar_is_not_the_same_as_no_holidays(self, session: Session) -> None:
        """They used to be the same mapping, and the difference is a real day.

        `LedgerService` queried the cache table directly, so an absent calendar
        looked exactly like a span with no holidays in it -- and a fresh install
        booked a full day's deficit against every bank holiday without a word.
        """
        svc = BankHolidayService(session, "england-and-wales")
        assert svc.titles_between(date(2026, 1, 1), date(2026, 12, 31)) is None

        session.add(
            BankHolidayCache(
                division="england-and-wales",
                date=date(2026, 12, 25),
                title="Christmas Day",
                fetched_at=datetime(2026, 1, 1),
            )
        )
        session.commit()

        found = svc.titles_between(date(2026, 1, 1), date(2026, 6, 30))
        assert found == {}, "a span with none in it is an empty mapping"
        assert svc.titles_between(date(2026, 1, 1), date(2026, 12, 31)) == {
            date(2026, 12, 25): "Christmas Day"
        }
