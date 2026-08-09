"""Tests for Slice 3: bank holiday cache and validation.

Covers: cache hit, stale refresh, fetch failure, division changes,
unavailable validation, title lookup.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from flexi.models.database.app import create_db_engine, get_session
from flexi.models.database.db import BankHolidayCache, Base
from flexi.services.bank_holidays import BankHolidayService

SAMPLE_RESPONSE = {
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


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_db_engine(tmp_path / "test.db")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    s = get_session(engine)
    yield s
    s.close()


def _seed_cache(session, division: str = "england-and-wales") -> None:
    """Insert sample bank holidays directly into the cache table."""
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    for ev in SAMPLE_RESPONSE.get(division, {}).get("events", []):
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
    def test_dates_from_fresh_cache(self, session) -> None:
        _seed_cache(session)
        svc = BankHolidayService(session, "england-and-wales")
        dates = svc.get_dates()
        assert dates is not None
        assert date(2026, 1, 1) in dates
        assert date(2026, 12, 25) in dates
        assert len(dates) == 3

    def test_is_bank_holiday(self, session) -> None:
        _seed_cache(session)
        svc = BankHolidayService(session, "england-and-wales")
        assert svc.is_bank_holiday(date(2026, 1, 1)) is True
        assert svc.is_bank_holiday(date(2026, 6, 15)) is False


# ---------- stale refresh ----------


class TestStaleRefresh:
    def test_stale_cache_triggers_refresh(self, session) -> None:
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
    def test_unavailable_when_no_cache(self, session) -> None:
        svc = BankHolidayService(session, "england-and-wales")
        assert svc.is_available() is False
        assert svc.get_dates() is None
        assert svc.is_bank_holiday(date(2026, 1, 1)) is None

    def test_fetch_failure_returns_false(self, session) -> None:
        svc = BankHolidayService(session, "england-and-wales")
        with patch(
            "flexi.services.bank_holidays.httpx.Client",
            side_effect=httpx.ConnectError("network"),
        ):
            assert svc.fetch_and_cache() is False


# ---------- division changes ----------


class TestDivisionChanges:
    def test_different_division_different_dates(self, session) -> None:
        _seed_cache(session, "england-and-wales")
        _seed_cache(session, "scotland")

        ew = BankHolidayService(session, "england-and-wales")
        sc = BankHolidayService(session, "scotland")

        assert ew.is_bank_holiday(date(2026, 12, 25)) is True
        assert sc.is_bank_holiday(date(2026, 12, 25)) is False
        assert sc.is_bank_holiday(date(2026, 11, 30)) is True


# ---------- title lookup ----------


class TestTitleLookup:
    def test_get_title(self, session) -> None:
        _seed_cache(session)
        svc = BankHolidayService(session, "england-and-wales")
        assert svc.get_title(date(2026, 12, 25)) == "Christmas Day"
        assert svc.get_title(date(2026, 6, 15)) is None
