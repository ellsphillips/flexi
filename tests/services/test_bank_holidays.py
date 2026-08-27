"""The bank holiday cache: hits, stale refreshes, and an index that is down.

Absence booking refuses outright when holiday data is unavailable, so the
difference between "no holidays" and "could not tell" has to survive the cache.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, TypedDict
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.orm import Session

from flexi.constants import Division
from flexi.models.database.db import BankHolidayCache
from flexi.services.bank_holidays import (
    BankHolidayService,
    ParsedBankHoliday,
    fetch_bank_holiday_index,
    parse_bank_holidays,
)


def reading(division: Division) -> Callable[[], Division]:
    """A service's division, fixed, for tests that are not about changing it."""
    return lambda: division


class Event(TypedDict):
    title: str
    date: str


class DivisionPayload(TypedDict):
    division: str
    events: list[Event]


SAMPLE_RESPONSE: dict[str, DivisionPayload] = {
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


def test_importing_the_service_does_not_import_the_http_client() -> None:
    """Cache-only commands keep the network stack off their startup path."""
    script = """
import sys

before = set(sys.modules)
import flexi.services.bank_holidays
introduced = set(sys.modules) - before
if "httpx" in introduced:
    raise AssertionError("importing bank_holidays eagerly imported httpx")
"""
    subprocess.run(  # noqa: S603 - fixed interpreter and in-repository script
        [sys.executable, "-c", script],
        check=True,
    )


def _answering(payload: object, status: int = 200) -> Callable[..., httpx.Response]:
    """A stand-in for GOV.UK, shaped like the real index.

    The suite refuses outbound requests, so a success path has to be handed one
    — and it is handed a real `httpx.Response`, because the code under test
    calls `raise_for_status()` and `json()` and a mock that only answers `json`
    would let a 503 through as a calendar.
    """

    def get(_self: httpx.Client, url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    return get


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
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        dates = svc.get_dates()
        assert dates is not None
        assert date(2026, 1, 1) in dates
        assert date(2026, 12, 25) in dates
        assert len(dates) == 3

    def test_holiday_on(self, session: Session) -> None:
        _seed_cache(session)
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        assert svc.holiday_on(date(2026, 1, 1)) is not None
        assert svc.holiday_on(date(2026, 6, 15)) is None


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

        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        assert svc.is_fresh() is False


# ---------- fetch failure ----------


class TestFetchFailure:
    def test_unavailable_when_no_cache(self, session: Session) -> None:
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        assert svc.is_available() is False
        assert svc.get_dates() is None
        assert svc.holiday_on(date(2026, 1, 1)) is None

    def test_fetch_failure_returns_false(self, session: Session) -> None:
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        with patch(
            "httpx.Client",
            side_effect=httpx.ConnectError("network"),
        ):
            assert svc.fetch_and_cache() is False

    def test_an_index_that_is_down_leaves_the_old_calendar_standing(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 503 is not news that the bank holidays were cancelled.

        The cache is cleared inside `fetch_and_cache`, so a refresh that got as
        far as a response and then failed on it must fail *before* the delete.
        Otherwise the weekly refresh, run on the morning GOV.UK is having a bad
        day, turns a working calendar into no calendar — and every leave
        booking is refused until it comes back.
        """
        _seed_cache(session)
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "get", _answering("", status=503))

        assert svc.fetch_and_cache() is False
        assert svc.holiday_on(date(2026, 12, 25)) is not None

    def test_a_response_that_is_not_json_is_a_failed_fetch(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A captive portal answers 200 with a login page, not a calendar."""
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))

        def html(_self: httpx.Client, url: str, **_kwargs: Any) -> httpx.Response:
            return httpx.Response(
                200, text="<html>sign in</html>", request=httpx.Request("GET", url)
            )

        monkeypatch.setattr(httpx.Client, "get", html)

        assert svc.fetch_and_cache() is False
        assert svc.is_available() is False


class TestFetchingTheIndex:
    """The success path, which the offline suite otherwise never walks."""

    def test_a_fetch_replaces_the_division_it_is_for(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replaces, rather than adds to.

        GOV.UK moves a substitute day when Christmas falls at a weekend, so a
        refresh that merged would leave the withdrawn date behind for ever and
        the calendar would slowly fill with holidays that are not holidays.
        """
        session.add(
            BankHolidayCache(
                division="england-and-wales",
                date=date(2026, 7, 4),
                title="Withdrawn",
                fetched_at=datetime(2020, 1, 1),
            )
        )
        session.commit()
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "get", _answering(SAMPLE_RESPONSE))

        assert svc.fetch_and_cache() is True
        assert svc.get_dates() == {
            date(2026, 1, 1),
            date(2026, 4, 3),
            date(2026, 12, 25),
        }
        assert svc.holiday_on(date(2026, 7, 4)) is None

    def test_only_the_division_asked_for_is_stored(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The index carries all three; St Andrew's Day is not an English holiday."""
        monkeypatch.setattr(httpx.Client, "get", _answering(SAMPLE_RESPONSE))

        BankHolidayService(session, reading(Division.SCOTLAND)).fetch_and_cache()

        assert BankHolidayService(session, reading(Division.SCOTLAND)).get_dates() == {
            date(2026, 1, 1),
            date(2026, 11, 30),
        }
        assert (
            BankHolidayService(session, reading(Division.ENGLAND_AND_WALES)).get_dates()
            is None
        )

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param([], id="the root is a list"),
            pytest.param({"england-and-wales": []}, id="the division is a list"),
            pytest.param(
                {"england-and-wales": {"events": None}},
                id="events is not a list",
            ),
            pytest.param(
                {"england-and-wales": {"events": [None]}}, id="an event is null"
            ),
            pytest.param(
                {
                    "england-and-wales": {
                        "events": [{"title": "Nonsense", "date": "someday"}]
                    }
                },
                id="a date is malformed",
            ),
            pytest.param(
                {
                    "england-and-wales": {
                        "events": [{"title": None, "date": "2026-01-01"}]
                    }
                },
                id="a title is malformed",
            ),
            pytest.param(
                {
                    "england-and-wales": {
                        "events": [
                            {"title": "First name", "date": "2026-01-01"},
                            {"title": "Second name", "date": "2026-01-01"},
                        ]
                    }
                },
                id="two events claim the same date",
            ),
            pytest.param({}, id="the configured division is missing"),
        ],
    )
    def test_a_malformed_response_leaves_the_old_calendar_standing(
        self,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        payload: object,
    ) -> None:
        """A partial new calendar is less trustworthy than the complete old one."""
        _seed_cache(session)
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "get", _answering(payload))

        assert svc.fetch_and_cache() is False
        assert svc.get_dates() == {
            date(2026, 1, 1),
            date(2026, 4, 3),
            date(2026, 12, 25),
        }

    def test_an_event_with_no_title_is_still_a_day_off(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The date is what the arithmetic needs; the name is decoration."""
        payload = {"england-and-wales": {"events": [{"date": "2026-01-01"}]}}
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "get", _answering(payload))

        assert svc.fetch_and_cache() is True
        assert svc.holiday_on(date(2026, 1, 1)) is not None
        assert svc.holiday_on(date(2026, 1, 1)) == ""

    def test_the_public_parser_returns_typed_immutable_events(self) -> None:
        assert parse_bank_holidays(SAMPLE_RESPONSE, Division.SCOTLAND) == (
            ParsedBankHoliday(date=date(2026, 1, 1), title="New Year's Day"),
            ParsedBankHoliday(date=date(2026, 11, 30), title="St Andrew's Day"),
        )

    def test_the_fetch_boundary_is_a_free_injectable_function(
        self, session: Session
    ) -> None:
        """Persistence depends on a callable, not on an HTTP client class."""
        asked = 0

        def fetch() -> object:
            nonlocal asked
            asked += 1
            return SAMPLE_RESPONSE

        service = BankHolidayService(
            session,
            reading(Division.SCOTLAND),
            fetch,
        )

        assert service.fetch_and_cache() is True
        assert asked == 1
        assert service.get_dates() == {date(2026, 1, 1), date(2026, 11, 30)}

    def test_the_default_fetch_boundary_is_public(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(httpx.Client, "get", _answering(SAMPLE_RESPONSE))
        assert fetch_bank_holiday_index() == SAMPLE_RESPONSE

    def test_an_explicitly_empty_calendar_is_valid(self) -> None:
        assert (
            parse_bank_holidays({"scotland": {"events": []}}, Division.SCOTLAND) == ()
        )

    def test_an_empty_cache_is_filled_from_the_index(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first run online: nothing cached, so it fetches and now answers."""
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "get", _answering(SAMPLE_RESPONSE))

        assert svc.fill_if_empty() is True
        assert svc.holiday_on(date(2026, 4, 3)) is not None


class TestRefreshingOnlyWhenItIsStale:
    """The two halves the launch worker composes.

    `app.FlexiApp.refresh_holidays` asks `is_fresh` and only then fetches, so
    that a fresh cache costs no round trip and a stale one is replaced. That
    composition is asserted in `tests/tui/test_app.py`; what is asserted here
    is that each half answers correctly on its own.
    """

    def test_an_empty_cache_counts_as_stale(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """There is no `fetched_at` to be young, and nothing to answer with."""
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        assert svc.is_fresh() is False

        monkeypatch.setattr(httpx.Client, "get", _answering(SAMPLE_RESPONSE))
        assert svc.fetch_and_cache() is True
        assert svc.holiday_on(date(2026, 1, 1)) is not None

    def test_a_fresh_cache_is_not_asked_for_again(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A week is the whole point of caching a list that changes once a year."""
        _seed_cache(session)
        asked: list[str] = []

        def counted(_self: httpx.Client, url: str, **_kwargs: Any) -> httpx.Response:
            asked.append(url)
            return httpx.Response(
                200, json=SAMPLE_RESPONSE, request=httpx.Request("GET", url)
            )

        monkeypatch.setattr(httpx.Client, "get", counted)

        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))

        assert svc.is_fresh() is True
        assert asked == [], "asking is free; only fetching is not"

    def test_a_stale_cache_is_refreshed(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stale means a substitute day may have moved since it was written."""
        session.add(
            BankHolidayCache(
                division="england-and-wales",
                date=date(2026, 7, 4),
                title="Withdrawn",
                fetched_at=(datetime.now(tz=UTC) - timedelta(days=10)).replace(
                    tzinfo=None
                ),
            )
        )
        session.commit()
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "get", _answering(SAMPLE_RESPONSE))

        assert svc.is_fresh() is False
        assert svc.fetch_and_cache() is True
        assert svc.get_dates() == {
            date(2026, 1, 1),
            date(2026, 4, 3),
            date(2026, 12, 25),
        }

    def test_a_stale_cache_offline_is_kept_rather_than_lost(
        self, session: Session
    ) -> None:
        """Last year's list beats no list at all when the train goes into a tunnel."""
        _seed_cache(session)
        session.query(BankHolidayCache).update(
            {"fetched_at": datetime(2020, 1, 1)},
        )
        session.commit()
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))

        assert svc.is_fresh() is False
        assert svc.fetch_and_cache() is False, "the refusal is reported"
        assert svc.holiday_on(date(2026, 12, 25)) is not None, "and nothing was lost"


# ---------- division changes ----------


class TestDivisionChanges:
    def test_different_division_different_dates(self, session: Session) -> None:
        _seed_cache(session, "england-and-wales")
        _seed_cache(session, "scotland")

        ew = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        sc = BankHolidayService(session, reading(Division.SCOTLAND))

        assert ew.holiday_on(date(2026, 12, 25)) is not None
        assert sc.holiday_on(date(2026, 12, 25)) is None
        assert sc.holiday_on(date(2026, 11, 30)) is not None


# ---------- title lookup ----------


class TestTitleLookup:
    def test_get_title(self, session: Session) -> None:
        _seed_cache(session)
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        assert svc.holiday_on(date(2026, 12, 25)) == "Christmas Day"
        assert svc.holiday_on(date(2026, 6, 15)) is None


class TestFillingTheCache:
    """Nothing in the application ever filled it.

    The refresh had no caller in `src/`; the only route to a populated cache
    was a Textual command-palette entry, so a person who used Flexi from the
    command line could not reach one. An empty cache is not a quiet state: every
    leave booking is refused, and every bank holiday is counted as a working day
    nobody worked.
    """

    def test_it_fetches_when_there_is_nothing_at_all(self, session: Session) -> None:
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
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
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))

        assert svc.is_fresh() is False, "the fixture should be stale"
        assert svc.fill_if_empty() is True, "and stale is good enough to keep"


class TestTellingEmptyFromAbsent:
    def test_no_calendar_is_not_the_same_as_no_holidays(self, session: Session) -> None:
        """They used to be the same mapping, and the difference is a real day.

        `LedgerService` queried the cache table directly, so an absent calendar
        looked exactly like a span with no holidays in it -- and a fresh install
        booked a full day's deficit against every bank holiday without a word.
        """
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
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
