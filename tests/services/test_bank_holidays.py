"""The bank holiday cache: hits, stale refreshes, and an index that is down.

Absence booking refuses outright when holiday data is unavailable, so the
difference between "no holidays" and "could not tell" has to survive the cache.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, TypedDict
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.orm import Session

from flexi import wallclock
from flexi.constants import Division
from flexi.models.database.db import (
    BankHolidayAttempt,
    BankHolidayCache,
    BankHolidayRefresh,
)
from flexi.services import bank_holidays
from flexi.services.bank_holidays import (
    CACHE_MAX_AGE,
    BankHolidayFetcher,
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

    A real `httpx.Response`, so status checks and streamed bytes use HTTPX's
    response handling.
    """

    def send(
        _self: httpx.Client, request: httpx.Request, **_kwargs: Any
    ) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request)

    return send


def _seed_cache(session: Session, division: str = "england-and-wales") -> None:
    """Insert sample bank holidays directly into the cache table."""
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    session.add(BankHolidayRefresh(division=division, fetched_at=now))
    known = SAMPLE_RESPONSE.get(division)
    for ev in known["events"] if known else ():
        session.add(
            BankHolidayCache(
                division=division,
                date=date.fromisoformat(ev["date"]),
                title=ev["title"],
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
    @pytest.mark.parametrize(
        ("age", "fresh"),
        [
            (CACHE_MAX_AGE - timedelta(minutes=1), True),
            (CACHE_MAX_AGE + timedelta(minutes=1), False),
        ],
        ids=("a-minute-inside", "a-minute-outside"),
    )
    def test_week_old_calendar_is_where_fresh_ends(
        self, session: Session, age: timedelta, fresh: bool
    ) -> None:
        """README promises a week of caching, so a minute either side pins it."""
        assert timedelta(days=7) == CACHE_MAX_AGE
        session.add(
            BankHolidayRefresh(
                division="england-and-wales",
                fetched_at=(wallclock.utc_now() - age).replace(tzinfo=None),
            )
        )
        session.commit()

        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))

        assert svc.is_fresh() is fresh

    def test_stale_cache_triggers_refresh(self, session: Session) -> None:
        old = datetime.now(tz=UTC) - CACHE_MAX_AGE - timedelta(days=3)
        session.add_all(
            (
                BankHolidayRefresh(
                    division="england-and-wales",
                    fetched_at=old.replace(tzinfo=None),
                ),
                BankHolidayCache(
                    division="england-and-wales",
                    date=date(2026, 1, 1),
                    title="Old",
                ),
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

    def test_index_that_is_down_keeps_the_old_calendar(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 503 is not news that the bank holidays were cancelled.

        The cache is cleared inside `fetch_and_cache`, so a refresh that fails
        on the response has to fail before the delete.
        """
        _seed_cache(session)
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "send", _answering("", status=503))

        assert svc.fetch_and_cache() is False
        assert svc.holiday_on(date(2026, 12, 25)) is not None

    def test_response_that_is_not_json_is_a_failed_fetch(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A captive portal answers 200 with a login page, not a calendar."""
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))

        def html(
            _self: httpx.Client, request: httpx.Request, **_kwargs: Any
        ) -> httpx.Response:
            return httpx.Response(200, text="<html>sign in</html>", request=request)

        monkeypatch.setattr(httpx.Client, "send", html)

        assert svc.fetch_and_cache() is False
        assert svc.is_available() is False


class TestFetchingTheIndex:
    """The success path, which the offline suite otherwise never walks."""

    def test_fetch_replaces_the_division_it_is_for(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refresh that merged would keep a withdrawn substitute day for ever."""
        session.add_all(
            (
                BankHolidayRefresh(
                    division="england-and-wales",
                    fetched_at=datetime(2020, 1, 1),
                ),
                BankHolidayCache(
                    division="england-and-wales",
                    date=date(2026, 7, 4),
                    title="Withdrawn",
                ),
            )
        )
        session.commit()
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "send", _answering(SAMPLE_RESPONSE))

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
        monkeypatch.setattr(httpx.Client, "send", _answering(SAMPLE_RESPONSE))

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
    def test_malformed_response_keeps_the_old_calendar(
        self,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        payload: object,
    ) -> None:
        """A partial new calendar is less trustworthy than the complete old one."""
        _seed_cache(session)
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "send", _answering(payload))

        assert svc.fetch_and_cache() is False
        assert svc.get_dates() == {
            date(2026, 1, 1),
            date(2026, 4, 3),
            date(2026, 12, 25),
        }

    def test_event_with_no_title_is_still_a_day_off(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The date is what the arithmetic needs; the name is decoration."""
        payload = {"england-and-wales": {"events": [{"date": "2026-01-01"}]}}
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "send", _answering(payload))

        assert svc.fetch_and_cache() is True
        assert svc.holiday_on(date(2026, 1, 1)) is not None
        assert svc.holiday_on(date(2026, 1, 1)) == ""

    def test_public_parser_returns_typed_immutable_events(self) -> None:
        assert parse_bank_holidays(SAMPLE_RESPONSE, Division.SCOTLAND) == (
            ParsedBankHoliday(date=date(2026, 1, 1), title="New Year's Day"),
            ParsedBankHoliday(date=date(2026, 11, 30), title="St Andrew's Day"),
        )

    def test_fetch_boundary_is_a_free_injectable_function(
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

    def test_supplied_payload_can_be_cached_without_fetching(
        self, session: Session
    ) -> None:
        """Hosts may separate network I/O from their persistence context."""

        def unexpected_fetch() -> object:
            pytest.fail("cache_payload must not invoke the fetch boundary")

        service = BankHolidayService(
            session,
            reading(Division.SCOTLAND),
            unexpected_fetch,
        )

        assert service.cache_payload(SAMPLE_RESPONSE) is True
        assert service.get_dates() == {date(2026, 1, 1), date(2026, 11, 30)}

    def test_invalid_payload_keeps_the_existing_calendar(
        self, session: Session
    ) -> None:
        _seed_cache(session, "scotland")
        service = BankHolidayService(session, reading(Division.SCOTLAND))
        before = service.get_dates()

        assert service.cache_payload(None) is False
        assert service.get_dates() == before

    def test_default_fetch_boundary_is_public(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(httpx.Client, "send", _answering(SAMPLE_RESPONSE))
        assert fetch_bank_holiday_index() == SAMPLE_RESPONSE

    def test_fetch_past_its_budget_is_unusable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`httpx` bounds connecting and reading; neither bound covers DNS.

        `getaddrinfo` runs inside `socket.create_connection` with no timeout of
        its own, so a resolver that has gone away holds the caller.
        """
        still_waiting = threading.Event()

        def answers_eventually(
            _self: httpx.Client, request: httpx.Request, **_kwargs: Any
        ) -> httpx.Response:
            still_waiting.wait(timeout=10)
            return httpx.Response(200, json=SAMPLE_RESPONSE, request=request)

        monkeypatch.setattr(bank_holidays, "_FETCH_BUDGET", 0.05)
        monkeypatch.setattr(httpx.Client, "send", answers_eventually)

        try:
            assert fetch_bank_holiday_index() is None
        finally:
            still_waiting.set()

    @pytest.mark.parametrize(
        "failure",
        [
            ImportError("Using SOCKS proxy, but 'socksio' is not installed"),
            FileNotFoundError(2, "No such file or directory"),
            IsADirectoryError(21, "Is a directory"),
            httpx.InvalidURL("Invalid port: 'abc'"),
        ],
        ids=[
            "socks-proxy",
            "missing-ca-file",
            "ca-file-is-a-directory",
            "bad-proxy-url",
        ],
    )
    def test_shell_that_breaks_the_client_is_unusable(
        self, failure: Exception, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The client is built from the environment, and can fail being built.

        `ALL_PROXY=socks5://...` without the socks extra raises `ImportError`,
        an `SSL_CERT_FILE` naming a removed bundle an `OSError`, a proxy URL
        with a bad port an `httpx.InvalidURL`. None is an `HTTPError`, and all
        come out of the constructor rather than the request.
        """

        def unbuildable(*_args: object, **_kwargs: object) -> None:
            raise failure

        monkeypatch.setattr(httpx.Client, "__init__", unbuildable)
        assert fetch_bank_holiday_index() is None

    def test_explicitly_empty_calendar_is_valid(self) -> None:
        assert (
            parse_bank_holidays({"scotland": {"events": []}}, Division.SCOTLAND) == ()
        )

    def test_successful_empty_calendar_has_persisted_cache_state(
        self, session: Session
    ) -> None:
        """No event rows is a known empty calendar, not an unavailable one."""
        _seed_cache(session, "scotland")
        fetches = 0

        def empty_index() -> object:
            nonlocal fetches
            fetches += 1
            return {"scotland": {"events": []}}

        svc = BankHolidayService(
            session,
            reading(Division.SCOTLAND),
            empty_index,
        )

        assert svc.fetch_and_cache() is True
        assert session.get(BankHolidayRefresh, Division.SCOTLAND.value) is not None
        assert svc.is_available() is True
        assert svc.is_fresh() is True
        assert svc.get_dates() == set()
        assert svc.titles_between(date(2026, 1, 1), date(2026, 12, 31)) == {}
        assert svc.fill_if_empty() is True
        assert fetches == 1, "known-empty metadata must prevent a second fetch"

    def test_empty_cache_is_filled_from_the_index(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first run online: nothing cached, so it fetches and now answers."""
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "send", _answering(SAMPLE_RESPONSE))

        assert svc.fill_if_empty() is True
        assert svc.holiday_on(date(2026, 4, 3)) is not None


class TestRefreshingOnlyWhenItIsStale:
    """The two halves the launch worker composes.

    `app.FlexiApp.refresh_holidays` asks `is_fresh` and only then fetches. The
    composition is asserted in `tests/tui/test_app.py`; each half is asserted
    here on its own.
    """

    def test_empty_cache_counts_as_stale(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """There is no `fetched_at` to be young, and nothing to answer with."""
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        assert svc.is_fresh() is False

        monkeypatch.setattr(httpx.Client, "send", _answering(SAMPLE_RESPONSE))
        assert svc.fetch_and_cache() is True
        assert svc.holiday_on(date(2026, 1, 1)) is not None

    def test_fresh_cache_is_not_asked_for_again(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A week is the whole point of caching a list that changes once a year."""
        _seed_cache(session)
        asked: list[str] = []

        def counted(
            _self: httpx.Client, request: httpx.Request, **_kwargs: Any
        ) -> httpx.Response:
            asked.append(str(request.url))
            return httpx.Response(200, json=SAMPLE_RESPONSE, request=request)

        monkeypatch.setattr(httpx.Client, "send", counted)

        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))

        assert svc.is_fresh() is True
        assert asked == [], "asking is free; only fetching is not"

    def test_stale_cache_is_refreshed(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stale means a substitute day may have moved since it was written."""
        stale = (wallclock.utc_now() - CACHE_MAX_AGE - timedelta(days=3)).replace(
            tzinfo=None
        )
        session.add_all(
            (
                BankHolidayRefresh(division="england-and-wales", fetched_at=stale),
                BankHolidayCache(
                    division="england-and-wales",
                    date=date(2026, 7, 4),
                    title="Withdrawn",
                ),
            )
        )
        session.commit()
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        monkeypatch.setattr(httpx.Client, "send", _answering(SAMPLE_RESPONSE))

        assert svc.is_fresh() is False
        assert svc.fetch_and_cache() is True
        assert svc.get_dates() == {
            date(2026, 1, 1),
            date(2026, 4, 3),
            date(2026, 12, 25),
        }

    def test_stale_cache_offline_is_kept(self, session: Session) -> None:
        """Last year's list beats no list at all when the train goes into a tunnel."""
        _seed_cache(session)
        session.query(BankHolidayRefresh).update(
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
    """`fill_if_empty`, the route to a populated cache from the command line.

    An empty cache refuses every leave booking and counts every bank holiday
    as an unworked working day.
    """

    def test_it_fetches_when_there_is_nothing_at_all(self, session: Session) -> None:
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        assert svc.is_available() is False

        # The suite refuses outbound requests, so this is the offline first run.
        assert svc.fill_if_empty() is False

    def test_it_does_not_fetch_over_an_existing_calendar(
        self, session: Session
    ) -> None:
        """A stale calendar answers correctly for the year it holds."""
        session.add_all(
            (
                BankHolidayRefresh(
                    division="england-and-wales",
                    fetched_at=datetime(2020, 1, 1),
                ),
                BankHolidayCache(
                    division="england-and-wales",
                    date=date(2020, 1, 1),
                    title="ancient",
                ),
            )
        )
        session.commit()
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))

        assert svc.is_fresh() is False, "the fixture should be stale"
        assert svc.fill_if_empty() is True, "and stale is good enough to keep"


def test_title_cannot_carry_instructions_to_a_terminal() -> None:
    """Sanitised where it is read, because the cached row is what every sink reads."""
    payload = {
        "england-and-wales": {
            "events": [
                {
                    "title": "Boxing Day" + chr(27) + "[31m PWNED" + chr(7) + chr(13),
                    "date": "2026-12-28",
                }
            ]
        }
    }

    parsed = parse_bank_holidays(payload, Division.ENGLAND_AND_WALES)

    assert parsed is not None
    assert parsed[0].title == "Boxing Day[31m PWNED", "the words stay; the codes go"


def unreachable(asked: list[str]) -> BankHolidayFetcher:
    """A GOV.UK with nothing to say, that counts how often it was asked."""

    def fetch() -> object | None:
        asked.append("gov.uk")
        return None

    return fetch


class TestNotAskingTwiceForTheSameSilence:
    """Opening the database fills an empty calendar, and every command opens it.

    A failed fetch is remembered, so the next command is not held for the whole
    budget again.
    """

    def test_failed_fetch_is_not_repeated_by_the_next_command(
        self, session: Session
    ) -> None:
        asked: list[str] = []
        svc = BankHolidayService(
            session, reading(Division.ENGLAND_AND_WALES), unreachable(asked)
        )

        assert svc.fill_if_empty() is False
        assert svc.fill_if_empty() is False
        assert asked == ["gov.uk"], "the second command asked again"

    def test_cooldown_runs_out(self, session: Session) -> None:
        """An hour later the machine may well be somewhere else."""
        asked: list[str] = []
        svc = BankHolidayService(
            session, reading(Division.ENGLAND_AND_WALES), unreachable(asked)
        )
        svc.fill_if_empty()

        stale = wallclock.utc_now() - bank_holidays.RETRY_AFTER - timedelta(minutes=1)
        attempt = session.get(BankHolidayAttempt, Division.ENGLAND_AND_WALES.value)
        assert attempt is not None
        attempt.attempted_at = stale.replace(tzinfo=None)
        session.commit()

        assert svc.fill_if_empty() is False
        assert len(asked) == 2

    def test_explicit_refresh_is_never_held_back(self, session: Session) -> None:
        """`flexi holidays refresh` is the user saying "try again, now"."""
        asked: list[str] = []
        svc = BankHolidayService(
            session, reading(Division.ENGLAND_AND_WALES), unreachable(asked)
        )

        assert svc.fetch_and_cache() is False
        assert svc.fetch_and_cache() is False
        assert len(asked) == 2

    def test_calendar_that_arrives_clears_the_cooldown(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        assert svc.fill_if_empty() is False, "the suite refuses outbound requests"
        assert svc.asked_recently() is True

        monkeypatch.setattr(httpx.Client, "send", _answering(SAMPLE_RESPONSE))
        assert svc.fetch_and_cache() is True

        assert svc.asked_recently() is False
        assert session.get(BankHolidayAttempt, Division.ENGLAND_AND_WALES.value) is None

    def test_one_division_does_not_silence_another(self, session: Session) -> None:
        """The cooldown is per calendar, because so is the cache."""
        asked: list[str] = []
        england = BankHolidayService(
            session, reading(Division.ENGLAND_AND_WALES), unreachable(asked)
        )
        scotland = BankHolidayService(
            session, reading(Division.SCOTLAND), unreachable(asked)
        )

        england.fill_if_empty()
        scotland.fill_if_empty()

        assert len(asked) == 2


class TestTellingEmptyFromAbsent:
    def test_no_calendar_is_not_the_same_as_no_holidays(self, session: Session) -> None:
        """`None` means no calendar; `{}` means a span with no holidays in it."""
        svc = BankHolidayService(session, reading(Division.ENGLAND_AND_WALES))
        assert svc.titles_between(date(2026, 1, 1), date(2026, 12, 31)) is None

        session.add_all(
            (
                BankHolidayRefresh(
                    division="england-and-wales",
                    fetched_at=datetime(2026, 1, 1),
                ),
                BankHolidayCache(
                    division="england-and-wales",
                    date=date(2026, 12, 25),
                    title="Christmas Day",
                ),
            )
        )
        session.commit()

        found = svc.titles_between(date(2026, 1, 1), date(2026, 6, 30))
        assert found == {}, "a span with none in it is an empty mapping"
        assert svc.titles_between(date(2026, 1, 1), date(2026, 12, 31)) == {
            date(2026, 12, 25): "Christmas Day"
        }
